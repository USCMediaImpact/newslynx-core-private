from gevent.pool import Pool

from copy import copy

from flask import Blueprint
from sqlalchemy import distinct

from newslynx.core import db
from newslynx.exc import NotFoundError
from newslynx.models import ContentItem, Author
from newslynx.lib.serialize import jsonify
from newslynx.views.decorators import load_user, load_org
from newslynx.tasks import facet
from newslynx.models.relations import content_items_events
from newslynx.views.util import *
from newslynx.constants import (
    CONTENT_ITEM_FACETS, CONTENT_ITEM_EVENT_FACETS)

# blueprint
bp = Blueprint('content', __name__)


# utils
content_facet_pool = Pool(len(CONTENT_ITEM_FACETS))


# TODO: Generalize this with `apply_event_filters`
def apply_content_item_filters(q, **kw):
    """
    Given a base ContentItem.query, apply all filters.
    """

    # filter by org_id
    q = q.filter(ContentItem.org_id == kw['org_id'])

    # use this for keeping track of
    # levels/categories events.
    all_event_ids = set()

    # apply search query
    if kw['search_query']:
        if kw['sort_field'] == 'relevance':
            sort = True
        else:
            sort = False
        if kw['search_vector'] == 'all':
            vector = ContentItem.title_search_vector | \
                ContentItem.description_search_vector | \
                ContentItem.body_search_vector | \
                Author.search_vector | \
                ContentItem.meta_search_vector

        elif kw['search_vector'] == 'authors':
            vector = Author.search_vector

        else:
            vname = "{}_search_vector".format(kw['search_vector'])
            vector = getattr(ContentItem, vname)

        q = q.search(kw['search_query'], vector=vector, sort=sort)

    # apply status filter
    if kw['type'] != 'all':
        q = q.filter(ContentItem.type == kw['type'])

    if kw['provenance']:
        q = q.filter(ContentItem.provenance == kw['provenance'])

    # filter url by regex
    if kw['url']:
        q = q.filter_by(url=kw['url'])

    # filter url by regex
    if kw['url_regex']:
        q = q.filter(text('content.url ~ :regex')).params(
            regex=kw['url_regex'])

    # filter by domain
    if kw['domain']:
        q = q.filter(ContentItem.domain == kw['domain'])

    # apply date filters
    if kw['created_after']:
        q = q.filter(ContentItem.created >= kw['created_after'])
    if kw['created_before']:
        q = q.filter(ContentItem.created <= kw['created_before'])
    if kw['updated_after']:
        q = q.filter(ContentItem.updated >= kw['updated_after'])
    if kw['updated_before']:
        q = q.filter(ContentItem.updated <= kw['updated_before'])

    # apply recipe filter
    if len(kw['include_recipes']):
        q = q.filter(ContentItem.recipe_id.in_(kw['include_recipes']))

    if len(kw['exclude_recipes']):
        q = q.filter(~ContentItem.recipe_id.in_(kw['exclude_recipes']))

    # apply tag categories/levels filter
    # TODO try not to use multiple queries here.
    if len(kw['include_categories']):

        event_ids = db.session.query(events_tags.c.event_id)\
            .join(Tag)\
            .filter_by(org_id=kw['org_id'])\
            .filter(Tag.category.in_(kw['include_categories']))\
            .all()

        event_ids = [e[0] for e in event_ids]
        for e in event_ids:
            all_event_ids.add(e)

        q = q.filter(ContentItem.events.any(
            Event.id.in_(event_ids)))

    if len(kw['exclude_categories']):

        event_ids = db.session.query(events_tags.c.event_id)\
            .join(Tag)\
            .filter_by(org_id=kw['org_id'])\
            .filter(Tag.category.in_(kw['exclude_categories']))\
            .all()
        event_ids = [e[0] for e in event_ids]
        for e in event_ids:
            all_event_ids.remove(e)
        q = q.filter(ContentItem.events.any(Event.id.in_(event_ids)))

    if len(kw['include_levels']):

        event_ids = db.session.query(events_tags.c.event_id)\
            .join(Tag)\
            .filter_by(org_id=kw['org_id'])\
            .filter(Tag.level.in_(kw['include_levels']))\
            .all()
        event_ids = [e[0] for e in event_ids]
        for e in event_ids:
            all_event_ids.add(e)
        q = q.filter(ContentItem.events.any(
            Event.id.in_(event_ids)))

    if len(kw['exclude_levels']):
        event_ids = db.session.query(events_tags.c.event_id)\
            .join(Tag)\
            .filter_by(org_id=kw['org_id'])\
            .filter(Tag.level.in_(kw['exclude_levels']))\
            .all()

        event_ids = [e[0] for e in event_ids]
        for e in event_ids:
            all_event_ids.remove(e)

        q = q.filter(ContentItem.events.any(
            Event.id.in_(event_ids)))

    # apply tags filter
    if len(kw['include_tags']):
        q = q.filter(ContentItem.tags.any(
            Tag.id.in_(kw['include_tags'])))

    if len(kw['exclude_tags']):
        q = q.filter(~ContentItem.tags.any(
            Tag.id.in_(kw['exclude_tags'])))

    # apply sous_chefs filter
    # TODO: DONT USE MULTIPLE QUERIES HERE
    if len(kw['include_sous_chefs']):
        sous_chef_recipes = db.session.query(Recipe.id)\
            .filter(Recipe.sous_chef.has(
                SousChef.name.in_(kw['include_sous_chefs'])))\
            .all()
        recipe_ids = [r[0] for r in sous_chef_recipes]
        q = q.filter(ContentItem.recipe_id.in_(recipe_ids))

    if len(kw['exclude_sous_chefs']):
        sous_chef_recipes = db.session.query(Recipe.id)\
            .filter(Recipe.sous_chef.has(
                SousChef.name.in_(kw['exclude_sous_chefs'])))\
            .all()
        recipe_ids = [r[0] for r in sous_chef_recipes]
        q = q.filter(~ContentItem.recipe_id.in_(recipe_ids))

    return q, list(all_event_ids)


# endpoints

@bp.route('/api/v1/content', methods=['GET'])
@load_user
@load_org
def search_content(user, org):
    """
    args:
        q              | search query
        url            | a regex for a url
        domain         | a domain to match on
        fields         | a comma-separated list of fields to include in response
        page           | page number
        per_page       | number of items per page.
        sort           | variable to order by, preface with '-' to sort desc.
        created_after  | isodate variable to filter results after
        created_before | isodate variable to filter results before
        updated_after  | isodate variable to filter results after
        updated_before | isodate variable to filter results before
        type           | ['pending', 'approved', 'deleted']
        facets         | a comma-separated list of facets to include, default=[]
        tag            | a comma-separated list of tags to filter by
        categories     | a comma-separated list of tag_categories to filter by
        levels         | a comma-separated list of tag_levels to filter by
        tag_ids        | a comma-separated list of content_items_ids to filter by
        recipe_ids     | a comma-separated list of recipes to filter by
        sous_chefs     | a comma-separated list of sous_chefs to filter by
        url_regex      | what does it sound like
        url            | duh
    """

    # parse arguments

    # store raw kwargs for generating pagination urls..
    raw_kw = dict(request.args.items())
    raw_kw['apikey'] = user.apikey
    raw_kw['org'] = org.id

    # special arg tuples
    sort_field, direction = \
        arg_sort('sort', default='-created')
    include_tags, exclude_tags = \
        arg_list('tag_ids', default=[], typ=int, exclusions=True)
    include_recipes, exclude_recipes = \
        arg_list('recipe_ids', default=[], typ=int, exclusions=True)
    include_sous_chefs, exclude_sous_chefs = \
        arg_list('sous_chefs', default=[], typ=str, exclusions=True)
    include_levels, exclude_levels = \
        arg_list('levels', default=[], typ=str, exclusions=True)
    include_categories, exclude_categories = \
        arg_list('categories', default=[], typ=str, exclusions=True)

    kw = dict(
        search_query=arg_str('q', default=None),
        search_vector=arg_str('search', default='all'),
        domain=arg_str('domain', default=None),
        fields=arg_list('fields', default=None),
        page=arg_int('page', default=1),
        per_page=arg_limit('per_page'),
        sort_field=sort_field,
        direction=direction,
        created_after=arg_date('created_after', default=None),
        created_before=arg_date('created_before', default=None),
        updated_after=arg_date('updated_after', default=None),
        updated_before=arg_date('updated_before', default=None),
        type=arg_str('type', default='all'),
        provenance=arg_str('provenance', default=None),
        incl_links=arg_bool('incl_links', default=False),
        facets=arg_list('facets', default=[], typ=str),
        include_categories=include_categories,
        exclude_categories=exclude_categories,
        include_levels=include_levels,
        exclude_levels=exclude_levels,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
        include_recipes=include_recipes,
        exclude_recipes=exclude_recipes,
        include_sous_chefs=include_sous_chefs,
        exclude_sous_chefs=exclude_sous_chefs,
        url=arg_str('url', default=None),
        url_regex=arg_str('url_regex', default=None),
        org_id=org.id
    )

    # validate arguments

    # validate sort fields are part of Event object.
    if kw['sort_field'] and kw['sort_field'] != 'relevance':
        validate_fields(
            ContentItem, fields=[kw['sort_field']], suffix='to sort by')

    # validate select fields.
    if kw['fields']:
        validate_fields(
            ContentItem, fields=kw['fields'], suffix='to select by')

    validate_tag_categories(kw['include_categories'])
    validate_tag_categories(kw['exclude_categories'])
    validate_tag_levels(kw['include_levels'])
    validate_tag_levels(kw['exclude_levels'])
    validate_content_item_types(kw['type'])
    validate_content_item_provenances(kw['provenance'])
    validate_content_item_search_vector(kw['search_vector'])

    # base query
    content_query = ContentItem.query

    # apply filters
    content_query, event_ids = \
        apply_content_item_filters(content_query, **kw)

    # select event fields
    if kw['fields']:
        cols = [eval('ContentItem.{}'.format(f)) for f in kw['fields']]
        content_query = content_query.with_entities(*cols)

    # apply sort if we havent already sorted by query relevance.
    if kw['sort_field'] != 'relevance':
        sort_obj = eval('ContentItem.{sort_field}.{direction}'.format(**kw))
        content_query = content_query.order_by(sort_obj())

    # facets
    validate_content_item_facets(kw['facets'])
    if kw['facets']:

        # set all facets
        if 'all' in kw['facets']:
            kw['facets'] = copy(CONTENT_ITEM_FACETS)

        # get all content_items ids for computing counts
        content_item_ids = content_query\
            .with_entities(ContentItem.id)\
            .all()
        content_item_ids = [t[0] for t in content_item_ids]

        # if we havent yet retrieved a list of event ids,
        # fetch this list only if the facets that require them
        # are included in the request
        if not len(event_ids):
            if any([f in kw['facets'] for f in CONTENT_ITEM_EVENT_FACETS]):
                event_ids = db.session.query(distinct(content_items_events.c.event_id))\
                    .filter(content_items_events.c.content_item_id.in_(content_item_ids))\
                    .group_by(content_items_events.c.event_id)\
                    .all()
                event_ids = [e[0] for e in event_ids]

        # pooled facet function
        def fx(by):
            if by in CONTENT_ITEM_EVENT_FACETS:
                if by == 'event_statuses':
                    return by, facet.events('statuses', event_ids)
                elif by == 'events':
                    return by, len(event_ids)
                return by, facet.events(by, event_ids)
            return by, facet.content_items(by, content_item_ids)

        # dict of results
        facets = {}
        for by, result in content_facet_pool.imap_unordered(fx, kw['facets']):
            facets[by] = result

    content = content_query\
        .paginate(kw['page'], kw['per_page'], False)

    # total results
    total = content.total

    # generate pagination urls
    pagination = \
        urls_for_pagination('content.search_content', total, **raw_kw)

    # reformat entites as dictionary
    if kw['fields']:
        content = [dict(zip(kw['fields'], r))
                   for r in content.items]
    else:
        content = [t.to_dict(incl_links=kw['incl_links'])
                   for t in content.items]

    resp = {
        'content_items': content,
        'pagination': pagination,
        'total': total
    }

    if len(kw['facets']):
        resp['facets'] = facets

    return jsonify(resp)


@bp.route('/api/v1/content/<int:content_item_id>', methods=['GET'])
@load_user
@load_org
def content_items(user, org, content_item_id):
    """
    Fetch an individual content-item.
    """
    c = ContentItem.query\
        .filter_by(id=content_item_id, org_id=org.id)\
        .first()
    if not c:
        raise NotFoundError(
            'An ContentItem with ID {} does not exist.'
            .format(event_id))
    return jsonify(c)
