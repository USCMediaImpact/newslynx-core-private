from urlparse import urljoin

# TODO: remove reliance on this library for oauth
import googleanalytics
from googleanalytics.auth import Credentials
from jinja2 import Template
from flask import (
    Blueprint, request, session, redirect, url_for
)

from newslynx import settings
from newslynx.core import db
from newslynx.models import Auth
from newslynx.exc import AuthError, RequestError
from newslynx.lib.serialize import jsonify
from newslynx.views.decorators import load_user, load_org
from newslynx.views.util import (
    obj_or_404, delete_response, request_data)
from newslynx.lib import url
from newslynx.util import here

# TODO: Figure out how to properly implement templates in flask blueprints.
# This may be a #wontfix since we only need this page.
templ_file = here(__file__, 'templates/ga_properties.html')
GA_PROP_TMPL = Template(open(templ_file).read())

# blueprint
bp = Blueprint('auth_google_analytics', __name__)

if settings.GA_ENABLED:
    # auth flow #
    ga_oauth = googleanalytics.auth.Flow(
        settings.GOOGLE_ANALYTICS_CLIENT_ID,
        settings.GOOGLE_ANALYTICS_CLIENT_SECRET,
        redirect_uri=urljoin(
            settings.API_URL,
            '/api/v1/auth/google-analytics/callback'))


# oauth utilities #

def ga_revoke_access(tokens):
    """
    Revoke a google analytics token.
    """
    tokens['client_id'] = settings.GOOGLE_ANALYTICS_CLIENT_ID
    tokens['client_secret'] = settings.GOOGLE_ANALYTICS_CLIENT_ID
    creds = Credentials(**tokens)
    creds.revoke()


def ga_properties(tokens):
    """
    Get a list of properties associated with a google analytics account.
    """
    accounts = googleanalytics.authenticate(**tokens)
    properties = []
    for account in accounts:
        for prop in account.webproperties:
            website_url = prop.url
            if website_url:
                property = {'property': website_url, 'profiles': []}
                for profile in prop.profiles:
                    property['profiles'].append(profile.name)
                properties.append(property)
    return properties


# GOOGLE ANALYTICS OAUTH ENDPOINTS #
@bp.route('/api/v1/auth/google-analytics', methods=['GET'])
@load_user
@load_org
def ga_auth(user, org):

    # raise error when configurations are not provided.
    if not settings.GA_ENABLED:
        raise AuthError(
            'You must provide a "google_analytics_client_id" and ' +
            '"google_analytics_client_secret in your ' +
            'NewsLynx configuration to enable Google Analytics integration. ' +
            'See https://developers.google.com/analytics/ for details on how to create ' +
            'an application on Google Analytics.')

    # store the user / apikey in the session:
    session['apikey'] = user.apikey
    session['org_id'] = org.id
    session['redirect_uri'] = request.args.get('redirect_uri')

    # Get Auth Url
    authorize_url = ga_oauth.step1_get_authorize_url()

    # Send the user to the auth URL.
    return redirect(authorize_url)


# callback
@bp.route('/api/v1/auth/google-analytics/callback')
def ga_callback():

    # get session vars
    apikey = session.get('apikey')
    org_id = session.get('org_id')
    redirect_uri = session.get('redirect_uri')

    # get tokens
    tokens = ga_oauth.step2_exchange(request.args['code']).serialize()

    # if we got didn't get refresh token,
    # it means the user is already authenticated
    # instead of just throwing an error, we'll revoke these
    # tokens if we have them and continue with the auth process.

    # a helper to prevent unnecessary db transactions

    if 'refresh_token' not in tokens or not tokens['refresh_token']:

        # get current auth
        ga_token = Auth.query\
            .filter_by(name='google-analytics', org_id=org_id)\
            .first()

        # if it doesn't exist, something has gone wrong, most likely on our
        # end.
        if not ga_token:
            if not redirect_uri:
                raise RequestError(
                    "It seems as if you've authenticated with google-analytics already, but we don't have a record of it. Try manually revoking your permissions at https://security.google.com/settings/security/permissions and re-authenticating.")
            uri = url.add_query_params(redirect_uri, auth_success='false')
            return redirect(uri)

        # if it does exist proceed with simulation of a normal auth flow and assume
        # we're simply updating a organization's property settings.
        tokens = ga_token.value
        tokens.update({
            'client_id': settings.GOOGLE_ANALYTICS_CLIENT_ID,
            'client_secret': settings.GOOGLE_ANALYTICS_CLIENT_SECRET,
        })
        tokens.pop('properties', None)

    # get properties
    properties = ga_properties(tokens)

    # now we can pop the client id + secret.
    tokens.pop('client_secret')
    tokens.pop('client_id')

    # get the postback url.
    postback_url = url_for(
        'auth_google_analytics.ga_save_properties',
        org=org_id,
        apikey=apikey)

    session['tokens'] = tokens

    # render customization form
    return GA_PROP_TMPL.render(
        properties=properties,
        postback_url=postback_url)


@bp.route('/api/v1/auth/google-analytics/properties', methods=['POST'])
@load_user
@load_org
def ga_save_properties(user, org):

    redirect_uri = session.pop('redirect_uri')
    tokens = session.pop('tokens')

    # PARSE HACKY FORM
    req_data = request_data()
    properties = []
    for k, v in req_data.items():
        prop = {
            'property': k.split('||')[0],
            'profile': v
        }
        properties.append(prop)

    tokens['properties'] = properties

    ga_token = Auth.query\
        .filter_by(name='google-analytics', org_id=org.id)\
        .first()

    if not ga_token:

        # create settings object
        ga_token = Auth(
            org_id=org.id,
            name='google-analytics',
            value=tokens)

    else:
        ga_token.value = tokens

    db.session.add(ga_token)
    db.session.commit()

    # redirect to app
    if redirect_uri:
        uri = url.add_query_params(redirect_uri, auth_success='true')
        redirect(uri)

    return jsonify(tokens)


@bp.route('/api/v1/auth/google-analytics/revoke', methods=['GET', 'DELETE'])
@load_user
@load_org
def ga_revoke(user, org):

    ga_token = Auth.query\
        .filter_by(org_id=org.id, name='google-analytics')\
        .first()

    obj_or_404(ga_token,
               'You have not authenticated yet with google-analytics.')

    token = ga_token.to_dict()['value']
    token.pop('properties')

    # revoke google analytics
    ga_revoke_access(token)

    # drop token from table
    db.session.delete(ga_token)
    db.session.commit()

    return delete_response()
