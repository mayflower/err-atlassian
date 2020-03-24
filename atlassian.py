# -*- coding: utf-8 -*-

import json
import logging
import re
from urllib.parse import urlparse
from functools import wraps

from opsdroid.skill import Skill
from opsdroid.matchers import match_regex

from markdown import markdown

#from errbot import BotPlugin, botcmd, re_botcmd, webhook
#from errbot.backends.slack import SlackBot
#from errbot.templating import tenv
#import errbot.backends.base
#from bottle import abort, response

from jira import JIRA, JIRAError
#import config
from .jira_oauth import JiraOauth

log = logging.getLogger(name='errbot.plugins.atlassian')

ATLASSIAN_EVENTS = [
    'connect_addon_disabled',
    'connect_addon_enabled',
    'jira:issue_created',
    'jira:issue_deleted',
    'jira:issue_updated',
    'jira:worklog_updated',
    'jira:version_created',
    'jira:version_deleted',
    'jira:version_merged',
    'jira:version_updated',
    'jira:version_moved',
    'jira:version_released',
    'jira:version_unreleased',
    'project_created',
    'project_updated',
    'project_deleted',
    'user_created',
    'user_deleted',
    'user_updated',
    'option_voting_changed',
    'option_watching_changed',
    'option_unassigned_issues_changed',
    'option_subtasks_changed',
    'option_attachments_changed',
    'option_issuelinks_changed',
    'option_timetracking_changed',
    'attachment_created',
    'attachment_removed',
    'attachment_restored',
    'attachment_trashed',
    'attachment_updated',
    'attachment_viewed',
    'blog_created',
    'blog_removed',
    'blog_restored',
    'blog_trashed',
    'blog_updated',
    'blog_viewed',
    'blueprint_page_created',
    'comment_created',
    'comment_removed',
    'comment_updated',
    'connect_addon_disabled',
    'connect_addon_enabled',
    'content_permissions_updated',
    'group_created',
    'group_removed',
    'label_added',
    'label_created',
    'label_deleted',
    'label_removed',
    'login',
    'login_failed',
    'logout',
    'page_children_reordered',
    'page_created',
    'page_moved',
    'page_removed',
    'page_restored',
    'page_trashed',
    'page_updated',
    'page_viewed',
    'search_performed',
    'space_created',
    'space_logo_updated',
    'space_permissions_updated',
    'space_removed',
    'space_updated',
    'user_created',
    'user_deactivated',
    'user_followed',
    'user_reactivated',
    'user_removed',
]

DEFAULT_EVENTS = ATLASSIAN_EVENTS

DEFAULT_CONFIG = { 'default_events': DEFAULT_EVENTS, 'projects': {}, }

HELP_MSG = ('Please see the output of `!atlassian help` for usage '
            'and configuration instructions.')

PROJECT_UNKNOWN = 'The project {0} is unknown to me.'
EVENT_UNKNOWN = 'Unknown event {0}, skipping.'

README = 'https://github.com/mayflower/err-atlassian/blob/master/README.rst'

def botcmd(*args, **kwargs):
    def wrap(fn):
        command = '!' + fn.__name__.replace('_', r'\s+')
        return re_botcmd(command, *args, **kwargs)(fn)
    return wrap

def re_botcmd(pattern, prefixed=True, matchall=False, admin_only=False, split_args_with=None):
    if split_args_with == None:
        split_args_with = '\s'
    def wrap(fn):
        wrapped = fn
        if not hasattr(fn, '_opsdroid_adapted'):
            @wraps(fn)
            async def wrapped(self, opsdroid, config, message, *args, **kwargs):
                if not message.user_id.endswith(':matrix.mayflower.de'):
                    return
                if admin_only and message.user_id not in self.config.get('admin_ids', ['linus.heckemann:matrix.mayflower.de']):
                    await message.respond("You're not allowed to do that!")
                    return
                args = re.split(split_args_with, re.sub(pattern, '', message.text, count=1))
                reply = fn(self, message, args)
                reply = markdown(reply, extensions=['nl2br'])
                if reply:
                    await message.respond(reply)
            wrapped._opsdroid_adapted = True
        wrapped = match_regex(pattern)(wrapped)
        return wrapped

    return wrap

def webhook(*args, **kwargs):
    def wrap(fn):
        return fn
    return wrap


class JiraNeedsAuthorization(Exception):
    pass

class Atlassian(Skill):

    min_err_version = '2.1.0'

    def get_configuration_template(self):
        return HELP_MSG

    def check_configuration(self, configuration):
        pass

    def configure(self, configuration):
        if configuration is not None:
            config = configuration
        else:
            config = DEFAULT_CONFIG
        super(Atlassian, self).configure(config)
    def __init__(self, opsdroid, config):
        _config = dict(DEFAULT_CONFIG)
        _config.update(config)
        super().__init__(opsdroid, _config)

    #################################################################
    # Convenience methods to get, check or set configuration options.
    #################################################################

    def clear_project(self, project):
        """Completely remove a project's configuration."""
        if self.has_project(project):
            self.config['projects'].pop(project)
            self.save_config()

    def clear_route(self, project, room):
        """Remove a route from a project."""
        if self.has_route(project, room):
            self.config['projects'][project]['routes'].pop(room)
            self.save_config()

    def has_project(self, project):
        """Check if the project is known."""
        if self.get_project(project) is None:
            return False
        else:
            return True

    def has_route(self, project, room):
        """Check if we have a route for this project to that room."""
        if self.get_route(project, room) is None:
            return False
        else:
            return True

    def get_defaults(self):
        """Return the default events that get relayed."""
        return self.config['default_events']

    def get_events(self, project, room):
        """Return all the events being relayed for this combination of
        project and room, aka a route.
        """
        return self.config['projects'].get(project, {}) \
                                          .get('routes', {}) \
                                          .get(room, {}) \
                                          .get('events')

    def get_project(self, project):
        """Return the project's configuration or None."""
        return self.config['projects'].get(project)

    def get_projects(self):
        """Return a list of all projects we have configured."""
        return list(self.config['projects'].keys())

    def get_route(self, project, room):
        """Return the configuration of this route."""
        return self.config['projects'].get(project, {}) \
                                          .get('routes', {}) \
                                          .get(room)

    def get_routes(self, project):
        """Fetch the routes for a project.
        Always check if the project exists before calling this.
        """
        return list(self.config['projects'].get(project, {}) \
                                          .get('routes', {}).keys())

    def set_defaults(self, defaults):
        """Set which events are relayed by default."""
        self.config['default_events'] = defaults
        self.save_config()

    def set_events(self, project, room, events):
        """Set the events to be relayed for this combination of project
        and room."""
        self.config['projects'][project]['routes'][room]['events'] = events
        self.save_config()

    def set_route(self, project, room):
        """Create a configuration entry for this route.

        If the project is unknown to us, add the project first.
        """
        if self.get_project(project) is None:
            self.config['projects'][project] = { 'routes': {} }
        self.config['projects'][project]['routes'][room] = {}
        self.save_config()

    def save_config(self):
        """Save the current configuration.

        This method takes care of saving the configuration since we can't
        use !config Atlassian <configuration blob> to configure this
        plugin.
        """
        self._bot.plugin_manager.set_plugin_configuration('Atlassian',
                                                          self.config)

    def show_project_config(self, project):
        """Builds up a complete list of rooms and events for a project."""
        if self.has_project(project):
            message = ['Routing {0} to:'.format(project)]
            for room in self.get_routes(project):
                message.append(' • {0} for events: {1}'.format(room, ' '.join(self.get_events(project, room))))
            return '\n'.join(message)
        else:
            return PROJECT_UNKNOWN.format(project)

    ###########################################################
    # Commands for the user to get, set or clear configuration.
    ###########################################################

    @re_botcmd('!atlassian')
    @botcmd()
    def atlassian_help(self, *args):
        """Output help."""
        message = []
        message.append('This plugin has multiple commands: ')
        message.append(' • config: to display the full configuration of '
                       'this plugin (not human friendly)')
        message.append(' • route <project> <room>: to relay messages from '
                       '<project> to <room> for events '
                       '{0}'.format(' '.join(self.get_defaults())))
        message.append(' • route <project> <room> <events>: to relay '
                       'messages from <project> to <room> for <events>')
        message.append(' • routes <project>: show routes for this project')
        message.append(' • routes: to display all routes')
        message.append(' • global route <room>: to set a route for global events')
        message.append(' • defaults <events>: to configure the events we '
                       'should forward by default')
        message.append(' • defaults: to show the events to be forwarded '
                       'by default')
        message.append('Please see {0} for more information.'.format(README))
        return '\n'.join(message)

    @botcmd(admin_only=True)
    def atlassian_config(self, *args):
        """Returns the current configuration of the plugin."""
        # pprint can't deal with nested dicts, json.dumps is aces.
        return json.dumps(self.config, indent=4, sort_keys=True)

    @botcmd(admin_only=True)
    def atlassian_reset(self, *args):
        """Nuke the complete configuration."""
        self.config = DEFAULT_CONFIG
        self.save_config()
        return 'Done. All configuration has been expunged.'

    @botcmd(split_args_with=None)
    def atlassian_defaults(self, message, args):
        """Get or set what events are relayed by default for new routes."""
        if args:
            events = []
            for event in args:
                if event in ATLASSIAN_EVENTS:
                    events.append(event)
                else:
                    yield EVENT_UNKNOWN.format(event)
            self.set_defaults(events)
            yield ('Done. Newly created routes will default to '
                   'receiving: {0}.'.format(' '.join(events)))
        else:
            yield ('Events routed by default: '
                   '{0}.'.format(' '.join(self.get_defaults())))

    @botcmd(split_args_with=None)
    def atlassian_route(self, message, args):
        """Map a project to a chatroom, essentially creating a route.

        This takes two or three arguments: author/project, a chatroom and
        optionally a list of events.

        If you do not specify a list of events the route will default to
        receiving the events configured as 'default_events'.
        """
        if len(args) >= 2:
            project = args[0]
            room = args[1]
            # Slicing on an index that, potentially, doesn't exist returns
            # an empty list instead of raising an IndexError
            events = args[2:]

            if not self.has_route(project, room):
                self.set_route(project, room)

            if events:
                for event in events[:]:
                    if event not in ATLASSIAN_EVENTS:
                        events.remove(event)
                        yield EVENT_UNKNOWN.format(event)
            else:
                events = self.get_defaults()
            self.set_events(project, room, events)
            yield ('Done. Relaying messages from {0} to {1} for '
                   'events: {2}'.format(project, room, ' '.join(events)))
        else:
            yield HELP_MSG

    @botcmd(split_args_with=None)
    def atlassian_routes(self, message, args):
        """Displays the routes for one, multiple or all projects."""
        if args:
            for project in args:
                if self.has_project(project):
                    yield self.show_project_config(project)
                else:
                    yield PROJECT_UNKNOWN.format(project)
        else:
            projects = self.get_projects()
            if projects:
                yield ("You asked for it, here are all the projects, the "
                       "rooms and associated events that are relayed:")
                for project in projects:
                    yield self.show_project_config(project)
            else:
                yield 'No projects configured, nothing to show.'


    @botcmd(split_args_with=None)
    def atlassian_remove(self, message, args):
        """Remove a route or a project.

        If only one argument is passed all configuration for that project
        is removed.

        When two arguments are passed that specific route is removed. If this
        was the last route any remaining configuration for the project is
        removed too. With only one route remaining this essentially achieves
        the same result as calling this with only the project as argument.
        """
        if len(args) == 1:
            project = args[0]
            self.clear_project(project)
            yield 'Removed all configuration for {0}.'.format(project)
        elif len(args) == 2:
            project = args[0]
            room = args[1]
            self.clear_route(project, room)
            yield 'Removed route for {0} to {1}.'.format(project, room)
            if not self.get_routes(project):
                self.clear_project(project)
                yield ('No more routes for {0}, removing remaining '
                       'configuration.'.format(project))
        else:
            yield HELP_MSG

    @botcmd(split_args_with=None)
    def atlassian_global(self, message, args):
        """Set a global route"""
        if len(args) == 1:
            self['global_route'] = None
            yield 'Removed global route.'
        elif len(args) == 2:
            room = args[1]
            self['global_route'] = room
            yield 'Set global route to {}.'.format(room)
        else:
            yield HELP_MSG

    def _handle_jira_auth(self, user):
        oauth = JiraOauth()
        link, state = oauth.request_token()
        self['oauth_request_{}'.format(user)] = state

        return link

    def _jira_req_auth(self, frm):
            link = self._handle_jira_auth(frm)
            text = 'To use the errbot JIRA integration please give permission at: {}'.format(link)
            self.send(self.build_identifier(frm), text)
            raise JiraNeedsAuthorization()

    def _jira_client(self, message):
        frm = getattr(message.frm, 'real_jid', message.frm.person)
        request_key = 'oauth_request_{}'.format(frm)
        access_key = 'oauth_access_{}'.format(frm)
        self.log.warn("FROM: %s", frm)
        if self.get(request_key):
            oauth = JiraOauth()
            state = self[request_key]
            try:
                self[access_key] = oauth.accepted(state)
            except KeyError:
                self._jira_req_auth(frm)
            del self[request_key]
        if not self.get(access_key):
            self._jira_req_auth(frm)
        token, secret = self[access_key]
        oauth_config = {
          'access_token': token,
          'access_token_secret': secret,
          'consumer_key': config.JIRA_OAUTH_KEY,
          'key_cert': config.JIRA_OAUTH_PEM,
        }

        return JIRA(config.JIRA_BASE_URL, oauth=oauth_config)

    @botcmd
    def jira_auth(self, message, args):
        '''Sends you the link to grant an OAuth token in JIRA'''
        if not message.is_direct:
            return "This has to be used in a direct message."
        return self._handle_jira_auth(message.frm.person)

    @botcmd
    def jira_forget(self, message, args):
        '''Deletes your JIRA OAuth token'''
        if not message.is_direct:
            return "This has to be used in a direct message."
        if self.get('oauth_access_{}'.format(message.frm.person)):
            del self['oauth_access_{}'.format(message.frm.person)]
        if self.get('oauth_request_{}'.format(message.frm.person)):
            del self['oauth_request_{}'.format(message.frm.person)]
        return 'Your OAuth token has been deleted.'

    @re_botcmd(pattern=r'\b[A-Z]+-[0-9]+\b', prefixed=False, matchall=True)
    def jira_issue(self, message, matches):
        '''Prints JIRA issue information if it recognizes an issue key'''
        if type(message.frm).__name__  == 'SlackRoomBot':
            return
        try:
            client = self._jira_client(message)
            for match in matches:
                try:
                    issue = client.issue(match.group())
                    epic_link_name = None
                    if issue.fields.customfield_10680:
                        epic_link = client.issue(issue.fields.customfield_10680)  # FIXME
                        epic_link_name = epic_link.fields.customfield_10681
                    issue_card = {
                      'to': getattr(message.frm, 'room', message.frm),
                      'summary': issue.fields.description,
                      'title': '{} - {}'.format(issue.key, issue.fields.summary),
                      'link': '{}/browse/{}'.format(config.JIRA_BASE_URL, issue.key),
                      'fields': [(k, v) for k, v in list({
                        'Assignee': getattr(issue.fields.assignee, 'displayName', None),
                        'Due Date': issue.fields.duedate,
                        'Reporter': getattr(issue.fields.reporter, 'displayName', None),
                        'Created': issue.fields.created,
                        'Priority': issue.fields.priority.name,
                        'Status': issue.fields.status.name,
                        'Resolution': getattr(issue.fields.resolution, 'name', None),
                        'Epic Link': epic_link_name,
                      }.items()) if v],
                    }
                    #yield str(issue.fields.components)
                    #yield str(issue.fields.issuelinks)
                    self.send_card(**issue_card)
                except JIRAError as err:
                    if err.status_code == 404:
                        yield 'No Issue {} found'.format(match.group())
        except JiraNeedsAuthorization:
            pass

    @webhook(r'/atlassian', methods=('POST',), raw=True)
    def receive(self, request):
        """Handle the incoming payload.

        Here be dragons.

        Validate the payload as best as we can and then delegate the creation
        of a sensible message to a function specific to this event. If no such
        function exists, use a generic message function.

        Once we have a message, route it to the appropriate channels.
        """

        log.info(request.json)
        if not self.validate_incoming(request):
            abort(400)

        body = request.json
        event_type = body['webhookEvent']

        project = body['issue']['fields']['project']['key'] if 'issue' in body else None
        global_event = self.is_global_event(event_type, project, body)

        if self.get_project(project) is None and not global_event:
            # Not a project we know so accept the payload, return 200 but
            # discard the message
            log.info('Message received for {0} but no such project '
                      'is configured'.format(project))
            response.status = 204
            return None

        message = self.dispatch_event(body, project, event_type)

        # - if we have a message and is it not empty or None
        # - get all rooms for the project we received the event for
        # - check if we should deliver this event
        # - join the room (this won't do anything if we're already joined)
        # - send the message
        if message and message is not None:
            for room_name in self.get_routes(project):
                events = self.get_events(project, room_name)
                if event_type in events or '*' in events:
                    self.join_and_send(room_name, message)
            if global_event and self.get('global_route'):
                self.join_and_send(self['global_route'], message)
        response.status = 204
        return None

    def join_and_send(self, room_name, message):
        room = self.query_room(room_name)
        try:
            room.join(username=self._bot.bot_config.CHATROOM_FN)
        except errbot.backends.base.RoomError as e:
            self.log.info(e)
        if isinstance(message, dict):
            self.send_card(
                to=room,
                **message
            )
        else:
            self.send(room, message)

    def is_global_event(self, event_type, project, body):
        return event_type in ['user_deleted']

    @staticmethod
    def validate_incoming(request):
        """Validate the incoming request:
          Check if the payload decodes to something we expect
        """
        try:
            body = request.json
        except ValueError:
            log.debug('ValueError while decoding JSON')
            return False

        if not isinstance(body, dict):
            log.debug('body is not a dict')
            return False

        return True

    def dispatch_event(self, body, project, event_type, generic_fn=None):
        """
        Dispatch the message. Check explicitly with hasattr first. When
        using a try/catch with AttributeError errors in the
        message_function which result in an AttributeError would cause
        us to call msg_generic, which is not what we want.
        """
        if generic_fn is None:
            generic_fn = self.msg_generic

        message_function = 'msg_{0}'.format(event_type.replace(':', '_'))
        if hasattr(self, message_function):
            message = getattr(self, message_function)(body, project)
        else:
            message = generic_fn(body, project, event_type)
        return message

    @staticmethod
    def msg_generic(body, project, event_type):
        return tenv().get_template('generic.html').render(locals().copy())

    @staticmethod
    def msg_issue_generic(body, project, event_type=None):
        summary = body['issue']['fields']['summary']
        url_parts = urlparse(body['issue']['self'])
        base_url = '{}://{}'.format(url_parts.scheme, url_parts.hostname)
        user = body['user']['displayName']
        key = body['issue']['key']
        if 'changelog' in body:
            url = '{}/browse/{}'.format(base_url, key)
            changes = []
            for item in body['changelog']['items']:
                field = item['field'][0].upper() + item['field'][1:]
                changes.append((field, '{} → {}'.format(item['fromString'], item['toString'])))

            return {
                'summary': '[jira] {} edited issue {}'.format(user, key),
                'title': '{} - {}'.format(key, summary),
                'link': url,
                'fields': changes,
                'body': body.get('comment', {}).get('body', '')
            }

        if 'comment' in body:
            url = '{base_url}/browse/{key}?focusedCommentId={commentId}&page=com.atlassian.jira.plugin.system.issuetabpanels:comment-tabpanel#comment-{commentId}'.format(
                base_url=base_url,
                key=key,
                commentId=body['comment']['id']
            )
            action = 'created' if event_type == 'issue_commented' else 'edited'

            return {
                'summary': '[jira] {} {} a comment on {}'.format(user, action, key),
                'title': '{} - {}'.format(key, summary),
                'link': url,
                'body': body['comment']['body']
            }

    def msg_jira_issue_updated(self, body, project):
        return self.dispatch_event(body, project, body['issue_event_type_name'], self.msg_issue_generic)

    @staticmethod
    def msg_jira_issue_created(body, project):
        url_parts = urlparse(body['issue']['self'])
        base_url = '{}://{}'.format(url_parts.scheme, url_parts.hostname)
        key = body['issue']['key']
        url = '{}/browse/{}'.format(base_url, key)
        user = body['user']['displayName']
        summary = body['issue']['fields']['summary']
        description = body['issue']['fields']['description']

        return {
            'summary': '[jira] {} created issue {}'.format(user, key),
            'title': '{} - {}'.format(key, summary),
            'link': url,
            'body': description
        }

    @staticmethod
    def msg_jira_issue_deleted(body, project):
        user = body['user']['displayName']
        key = body['issue']['key']
        summary = body['issue']['fields']['summary']

        return '[jira] {} deleted an issue {} - {}'.format(user, key, summary)

    @staticmethod
    def msg_issue_comment_deleted(body, project):
        url_parts = urlparse(body['issue']['self'])
        base_url = '{}://{}'.format(url_parts.scheme, url_parts.hostname)
        user = body['user']['displayName']
        key = body['issue']['key']
        url = '{}/browse/{}'.format(base_url, key)

        return '[jira] {} deleted a comment on {} ({})'.format(user, key, url)

    @staticmethod
    def msg_user_deleted(body, project):
        user = body['user']['name']

        return '[jira] User {} was deleted'.format(user)
