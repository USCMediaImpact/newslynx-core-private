from sqlalchemy.dialects.postgresql import JSON, ENUM
from sqlalchemy_utils.types import TSVectorType
from sqlalchemy import Index

from newslynx.core import db, SearchQuery
from newslynx.lib import dates
from newslynx.models import relations
from newslynx.constants import (
    CONTENT_ITEM_TYPES, CONTENT_ITEM_PROVENANCES)


class ContentItem(db.Model):

    """
    A content-item is a unit of content
    to which we attach metrics.

    We do not initialize a content-item until we have past it completely through
    our single ingestion pipeline.

    At this point all content-items should have a standardized schema,
    though may not have all theses fields filled in.
    """

    query_class = SearchQuery

    __tablename__ = 'content'

    # the ID is the global bitly hash.
    id = db.Column(db.Integer, unique=True, primary_key=True, index=True)
    org_id = db.Column(
        db.Integer, db.ForeignKey('orgs.id'), index=True)
    recipe_id = db.Column(db.Integer, db.ForeignKey('recipes.id'), index=True)
    type = db.Column(ENUM(*CONTENT_ITEM_TYPES, name='content_item_types_enum'))
    provenance = db.Column(
        ENUM(*CONTENT_ITEM_PROVENANCES, name='content_item_provenance_enum'), index=True)
    url = db.Column(db.Text, index=True)
    domain = db.Column(db.Text, index=True)
    created = db.Column(db.DateTime(timezone=True), default=dates.now)
    updated = db.Column(db.DateTime(timezone=True), onupdate=dates.now, default=dates.now)
    site_name = db.Column(db.Text, index=True)
    favicon = db.Column(db.Text)
    img_url = db.Column(db.Text)
    title = db.Column(db.Text)
    description = db.Column(db.Text)
    body = db.Column(db.Text)
    meta = db.Column(JSON)

    # relations
    tags = db.relationship(
        'Tag', secondary=relations.content_items_tags,
        backref=db.backref('content_items', lazy='dynamic'), lazy='joined')

    events = db.relationship(
        'Event',
        secondary=relations.content_items_events,
        backref=db.backref('content_items', lazy='dynamic'),
        lazy='dynamic')

    authors = db.relationship(
        'Author', secondary=relations.content_items_authors,
        backref=db.backref('content_items', lazy='dynamic'), lazy='joined')

    # # in/out links
    # out_links = db.relationship(
    #     'ContentItem', secondary=relations.content_items_content_items,
    #     primaryjoin=relations.content_items_content_items.c.from_content_item_id == id,
    #     secondaryjoin=relations.content_items_content_items.c.to_content_item_id == id,
    #     backref=db.backref("in_links", lazy='dynamic'),
    #     lazy='dynamic')

    # search vectors
    title_search_vector = db.Column(TSVectorType('title'))
    body_search_vector = db.Column(TSVectorType('body'))
    description_search_vector = db.Column(TSVectorType('description'))
    meta_search_vector = db.Column(TSVectorType('meta'))

    # content_items should be unique to org, url, and type.
    # IE there might be multiple content_items per url -
    # an article, a video, a podcast, etc.
    __table_args__ = (
        db.UniqueConstraint(
            'org_id', 'url', 'type', name='content_item_unique_constraint'),
        Index('content_item_title_search_vector_idx',
              'title_search_vector', postgresql_using='gin'),
        Index('content_item_body_search_vector_idx',
              'body_search_vector', postgresql_using='gin'),
        Index('content_item_description_search_vector_idx',
              'description_search_vector', postgresql_using='gin'),
        Index('content_item_meta_search_vector_idx',
              'meta_search_vector', postgresql_using='gin')
    )

    def __init__(self, **kw):
        self.org_id = kw.get('org_id')
        self.recipe_id = kw.get('recipe_id')
        self.url = kw.get('url')
        self.type = kw.get('type')
        self.provenance = kw.get('provenance', 'recipe')
        self.domain = kw.get('domain')
        self.created = kw.get('created')
        self.site_name = kw.get('site_name')
        self.favicon = kw.get('favicon')
        self.img_url = kw.get('img_url')
        self.title = kw.get('title')
        self.description = kw.get('description')
        self.body = kw.get('body')
        self.meta = kw.get('meta', {})

    @property
    def simple_authors(self):
        return [{"id": c.id, "name": c.name} for c in self.authors]

    # @property
    # def out_link_ids(self):
    #     out_links = db.session.query(relations.content_items_content_items.c.to_content_item_id)\
    #         .filter(relations.content_items_content_items.c.from_content_item_id == self.id)\
    #         .all()
    #     return [o[0] for o in out_links]

    # @property
    # def in_link_ids(self):
    #     in_links = db.session.query(relations.content_items_content_items.c.from_content_item_id)\
    #         .filter(relations.content_items_content_items.c.to_content_item_id == self.id)\
    #         .all()
    #     return [o[0] for o in in_links]

    # @property
    # def out_link_display(self):
    #     out_links = self.out_links\
    #         .with_entities(ContentItem.id, ContentItem.title)\
    #         .all()
    #     return [dict(zip(['id', 'title'], l)) for l in out_links]

    # @property
    # def in_link_display(self):
    #     in_links = self.in_links\
    #         .with_entities(ContentItem.id, ContentItem.title)\
    #         .all()
    #     return [dict(zip(['id', 'title'], l)) for l in in_links]

    @property
    def tag_ids(self):
        return [t.id for t in self.tags]

    @property
    def author_ids(self):
        return [t.id for t in self.authors]

    def to_dict(self, **kw):
        # incl_links = kw.get('incl_links', False)
        incl_body = kw.get('incl_body', False)

        d = {
            'id': self.id,
            'org_id': self.org_id,
            'recipe_id': self.recipe_id,
            'url': self.url,
            'domain': self.domain,
            'provenance': self.provenance,
            'type': self.type,
            'created': self.created,
            'updated': self.updated,
            'favicon': self.favicon,
            'site_name': self.site_name,
            'img_url': self.img_url,
            'authors': self.simple_authors,
            'title': self.title,
            'description': self.description,
            'tag_ids': self.tag_ids,
            'meta': self.meta
        }
        # if incl_links:
        #     d['in_links'] = self.in_link_display
        #     d['out_links'] = self.out_link_display
        if incl_body:
            d['body'] = self.body
        return d

    def __repr__(self):
        return '<ContentItem %r /  %r >' % (self.url, self.type)
