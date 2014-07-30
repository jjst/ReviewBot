import re

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.http import HttpRequest
from django.utils.importlib import import_module

from celery import Celery
from djblets.siteconfig.models import SiteConfiguration
from reviewboard.reviews.models import ReviewRequest
from reviewboard.extensions.base import Extension

from reviewbotext.handlers import SignalHandlers
from reviewbotext.models import ReviewBotTool
from reviewbotext.resources import review_bot_review_resource, \
                                   review_bot_tool_resource
import logging

class ReviewBotExtension(Extension):
    """An extension for communicating with Review Bot"""
    metadata = {
        'Name': 'Review Bot',
        'Summary': 'Performs automated analysis and review on code posted '
                   'to Review Board.',
        'Author': 'Review Board',
        'Author-URL': 'http://www.reviewboard.org/',
    }

    is_configurable = True
    has_admin_site = True

    default_settings = {
        'ship_it': False,
        'comment_unmodified': False,
        'open_issues': False,
        'BROKER_URL': '',
        'user': None,
        'max_comments': 30,
    }

    resources = [
        review_bot_review_resource,
        review_bot_tool_resource,
    ]

    def initialize(self):
        self.celery = Celery('reviewbot.tasks')
        SignalHandlers(self)

    def notify(self, request_payload):
        """Add the request to the queue."""
        self.celery.conf.BROKER_URL = self.settings['BROKER_URL']

        review_settings = {
            'max_comments': self.settings['max_comments'],
        }
        payload = {
            'request': request_payload,
            'review_settings': review_settings,
            'session': self._login_user(self.settings['user']),
            'url': self._rb_url(),
        }
        review_request_id = request_payload['review_request_id']
        review_request = ReviewRequest.objects.get(pk=review_request_id)
        review_request_summary = review_request.summary
        tools = ReviewBotTool.objects.filter(enabled=True,
                                             run_automatically=True)


        for tool in tools:
            if (tool.reviews_to_skip and
                 re.match(tool.reviews_to_skip, review_request_summary)):
                logging.debug("Skipping tool %s" % tool)
            else:
                review_settings['ship_it'] = tool.ship_it
                review_settings['comment_unmodified'] = tool.comment_unmodified
                review_settings['open_issues'] = tool.open_issues
                payload['review_settings'] = review_settings

                try:
                    self.celery.send_task(
                        "reviewbot.tasks.ProcessReviewRequest",
                        [payload, tool.tool_settings],
                        queue='%s.%s' % (tool.entry_point, tool.version))
                except:
                    raise

    def _login_user(self, user_id):
        """
        Login as specified user, does not depend on auth backend (hopefully).

        This is based on Client.login() with a small hack that does not
        require the call to authenticate().

        Will return the session id of the login.
        """
        user = User.objects.get(pk=user_id)
        user.backend = 'reviewboard.accounts.backends.StandardAuthBackend'
        engine = import_module(settings.SESSION_ENGINE)

        # Create a fake request to store login details.
        request = HttpRequest()
        request.session = engine.SessionStore()
        login(request, user)
        request.session.save()
        return request.session.session_key

    def send_refresh_tools(self):
        """Request workers to update tool list."""
        self.celery.conf.BROKER_URL = self.settings['BROKER_URL']
        payload = {
            'session': self._login_user(self.settings['user']),
            'url': self._rb_url(),
        }
        self.celery.control.broadcast('update_tools_list', payload=payload)

    def _rb_url(self):
        """Returns a valid reviewbot url including http protocol."""
        protocol = SiteConfiguration.objects.get_current().get(
            "site_domain_method")
        domain = Site.objects.get_current().domain
        return '%s://%s%s' % (protocol, domain, settings.SITE_ROOT)
