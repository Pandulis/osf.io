from modularodm import Q
from modularodm.exceptions import NoResultsFound
import mock
import unittest
import datetime
import collections
import pytz
from mako.lookup import Template
from tests.base import OsfTestCase, capture_signals
from nose.tools import *  # PEP8 asserts
from framework.auth.signals import contributor_removed, node_deleted
from framework.auth import Auth
from website.util import web_url_for
from website.notifications.model import Subscription, DigestNotification
from website.notifications.constants import SUBSCRIPTIONS_AVAILABLE, NOTIFICATION_TYPES, USER_SUBSCRIPTIONS_AVAILABLE
from website.notifications import emails, utils
from website.util import api_url_for
from website import mails
from tests.factories import ProjectFactory, NodeFactory, UserFactory, SubscriptionFactory, CommentFactory


class TestSubscriptionView(OsfTestCase):
    def test_create_new_subscription(self):
        node = NodeFactory()
        payload = {
            'id': node._id,
            'event': 'comments',
            'notification_type': 'email_transactional'
        }
        url = api_url_for('configure_subscription')
        self.app.post_json(url, payload, auth=node.creator.auth)

        # check that subscription was created
        event_id = node._id + '_' + 'comments'
        s = Subscription.find_one(Q('_id', 'eq', event_id))

        # check that user was added to notification_type field
        assert_equal(payload['id'], s.owner._id)
        assert_equal(payload['event'], s.event_name)
        assert_in(node.creator, getattr(s, payload['notification_type']))

        # change subscription
        new_payload = {
            'id': node._id,
            'event': 'comments',
            'notification_type': 'email_digest'
        }
        url = api_url_for('configure_subscription')
        self.app.post_json(url, new_payload, auth=node.creator.auth)
        s.reload()
        assert_false(node.creator in getattr(s, payload['notification_type']))
        assert_in(node.creator, getattr(s, new_payload['notification_type']))

    def test_adopt_parent_subscription_default(self):
        node = NodeFactory()
        payload = {
            'id': node._id,
            'event': 'comments',
            'notification_type': 'adopt_parent'
        }
        url = api_url_for('configure_subscription')
        self.app.post_json(url, payload, auth=node.creator.auth)
        event_id = node._id + '_' + 'comments'
        # confirm subscription was not created
        with assert_raises(NoResultsFound):
            Subscription.find_one(Q('_id', 'eq', event_id))

    def test_change_subscription_to_adopt_parent_subscription_removes_user(self):
        node = NodeFactory()
        payload = {
            'id': node._id,
            'event': 'comments',
            'notification_type': 'email_transactional'
        }
        url = api_url_for('configure_subscription')
        self.app.post_json(url, payload, auth=node.creator.auth)

        # check that subscription was created
        event_id = node._id + '_' + 'comments'
        s = Subscription.find_one(Q('_id', 'eq', event_id))

        # change subscription to adopt_parent
        new_payload = {
            'id': node._id,
            'event': 'comments',
            'notification_type': 'adopt_parent'
        }
        url = api_url_for('configure_subscription')
        self.app.post_json(url, new_payload, auth=node.creator.auth)
        s.reload()

        # assert that user is removed from the subscription entirely
        for n in NOTIFICATION_TYPES:
            assert_false(node.creator in getattr(s, n))


class TestRemoveContributor(OsfTestCase):
    def setUp(self):
        super(OsfTestCase, self).setUp()
        self.project = ProjectFactory()
        self.contributor = UserFactory()
        self.project.add_contributor(contributor=self.contributor, permissions=['read'])
        self.project.save()

        self.subscription = SubscriptionFactory(
            _id=self.project._id + '_comments',
            owner=self.project
        )
        self.subscription.save()
        self.subscription.email_transactional.append(self.contributor)
        self.subscription.email_transactional.append(self.project.creator)
        self.subscription.save()

        self.node = NodeFactory(project=self.project)
        self.node.add_contributor(contributor=self.project.creator, permissions=['read', 'write', 'admin'])
        self.node.save()
        self.node_subscription = SubscriptionFactory(
            _id=self.node._id + '_comments',
            owner=self.node
        )
        self.node_subscription.save()
        self.node_subscription.email_transactional.append(self.project.creator)
        self.node_subscription.email_transactional.append(self.node.creator)
        self.node_subscription.save()

    def test_removed_non_admin_contributor_is_removed_from_subscriptions(self):
        assert_in(self.contributor, self.subscription.email_transactional)
        self.project.remove_contributor(self.contributor, auth=Auth(self.project.creator))
        assert_not_in(self.contributor, self.project.contributors)
        assert_not_in(self.contributor, self.subscription.email_transactional)

    def test_removed_non_parent_admin_contributor_is_removed_from_subscriptions(self):
        assert_in(self.node.creator, self.node_subscription.email_transactional)
        self.node.remove_contributor(self.node.creator, auth=Auth(self.node.creator))
        assert_not_in(self.node.creator, self.node.contributors)
        assert_not_in(self.node.creator, self.node_subscription.email_transactional)

    def test_removed_contributor_admin_on_parent_not_removed_from_node_subscription(self):
        """ Admin on parent project is removed as a contributor on a component. Check
            that admin is not removed from component subscriptions, as the admin
            now has read-only access.
        """
        assert_in(self.project.creator, self.node_subscription.email_transactional)
        self.node.remove_contributor(self.project.creator, auth=Auth(self.project.creator))
        assert_not_in(self.project.creator, self.node.contributors)
        assert_in(self.project.creator, self.node_subscription.email_transactional)

    def test_remove_contributor_signal_called_when_contributor_is_removed(self):
        with capture_signals() as mock_signals:
            self.project.remove_contributor(self.contributor, auth=Auth(self.project.creator))
        assert_equal(mock_signals.signals_sent(), set([contributor_removed]))


class TestRemoveNodeSignal(OsfTestCase):
        def test_node_subscriptions_and_backrefs_removed_when_node_is_deleted(self):
            project = ProjectFactory()
            subscription = SubscriptionFactory(
                _id=project._id + '_comments',
                owner=project
            )
            subscription.save()
            subscription.email_transactional.append(project.creator)
            subscription.save()

            s = getattr(project.creator, 'email_transactional', [])
            assert_equal(len(s), 1)

            with capture_signals() as mock_signals:
                project.remove_node(auth=Auth(project.creator))
            assert_true(project.is_deleted)
            assert_equal(mock_signals.signals_sent(), set([node_deleted]))

            s = getattr(project.creator, 'email_transactional', [])
            assert_equal(len(s), 0)

            with assert_raises(NoResultsFound):
                Subscription.find_one(Q('owner', 'eq', project))


class TestNotificationUtils(OsfTestCase):
    def setUp(self):
        super(TestNotificationUtils, self).setUp()
        self.user = UserFactory()
        self.project = ProjectFactory(creator=self.user)
        self.project_subscription = SubscriptionFactory(
            _id=self.project._id + '_' + 'comments',
            owner=self.project,
            event_name='comments'
        )
        self.project_subscription.save()
        self.project_subscription.email_transactional.append(self.user)
        self.project_subscription.save()

        self.node = NodeFactory(project=self.project, creator=self.user)
        self.node_subscription = SubscriptionFactory(
            _id=self.node._id + '_' + 'comments',
            owner=self.node,
            event_name='comments'
        )
        self.node_subscription.save()
        self.node_subscription.email_transactional.append(self.user)
        self.node_subscription.save()

        self.user_subscription = SubscriptionFactory(
            _id=self.user._id + '_' + 'comment_replies',
            owner=self.user,
            event_name='comment_replies'
        )
        self.user_subscription.save()
        self.user_subscription.email_transactional.append(self.user)
        self.user_subscription.save()

    def test_to_subscription_key(self):
        key = utils.to_subscription_key('xyz', 'comments')
        assert_equal(key, 'xyz_comments')

    def test_from_subscription_key(self):
        parsed_key = utils.from_subscription_key('xyz_comment_replies')
        assert_equal(parsed_key, {
            'uid': 'xyz',
            'event': 'comment_replies'
        })

    def test_get_all_user_subscriptions(self):
        user_subscriptions = utils.get_all_user_subscriptions(self.user)
        assert_in(self.project_subscription, user_subscriptions)
        assert_in(self.node_subscription, user_subscriptions)
        assert_in(self.user_subscription, user_subscriptions)
        assert_equal(len(utils.get_all_user_subscriptions(self.user)), 3)

    def test_get_all_node_subscriptions_given_user_subscriptions(self):
        user_subscriptions = utils.get_all_user_subscriptions(self.user)
        node_subscriptions = utils.get_all_node_subscriptions(self.user, self.node, user_subscriptions=user_subscriptions)
        assert_equal(node_subscriptions, [self.node_subscription])

    def test_get_all_node_subscriptions_given_user_and_node(self):
        node_subscriptions = utils.get_all_node_subscriptions(self.user, self.node)
        assert_equal(node_subscriptions, [self.node_subscription])

    def test_get_configured_project_ids_does_not_return_user_or_node_ids(self):
        configured_ids = utils.get_configured_projects(self.user)

        assert_in(self.project._id, configured_ids)
        assert_not_in(self.node._id, configured_ids)
        assert_not_in(self.user._id, configured_ids)

    def test_get_configured_project_ids_excludes_deleted_projects(self):
        project = ProjectFactory()
        subscription = SubscriptionFactory(
            _id=project._id + '_' + 'comments',
            owner=project
        )
        subscription.save()
        subscription.email_transactional.append(self.user)
        subscription.save()
        project.is_deleted = True
        project.save()
        assert_not_in(project._id, utils.get_configured_projects(self.user))

    def test_get_configured_project_ids_excludes_node_with_project_category(self):
        node = NodeFactory(project=self.project, category='project')
        node_subscription = SubscriptionFactory(
            _id=node._id + '_' + 'comments',
            owner=node,
            event_name='comments'
        )
        node_subscription.save()
        node_subscription.email_transactional.append(self.user)
        node_subscription.save()
        assert_not_in(node._id, utils.get_configured_projects(self.user))

    def test_get_configured_project_ids_includes_top_level_private_projects_if_subscriptions_on_node(self):
        private_project = ProjectFactory()
        node = NodeFactory(project=private_project)
        node_subscription = SubscriptionFactory(
            _id=node._id + '_comments',
            owner=node,
            event_name='comments'
        )
        node_subscription.email_transactional.append(node.creator)
        node_subscription.save()
        configured_project_ids = utils.get_configured_projects(node.creator)
        assert_in(private_project._id, configured_project_ids)

    def test_get_configured_project_ids_excludes_private_projects_if_no_subscriptions_on_node(self):
        private_project = ProjectFactory()
        node = NodeFactory(project=private_project)
        configured_project_ids = utils.get_configured_projects(node.creator)
        assert_not_in(private_project._id, configured_project_ids)

    def test_get_parent_notification_type(self):
        nt = utils.get_parent_notification_type(self.node._id, 'comments', self.user)
        assert_equal(nt, 'email_transactional')

    def test_get_parent_notification_type_no_parent_subscriptions(self):
        node = NodeFactory()
        nt = utils.get_parent_notification_type(node._id, 'comments', self.user)
        assert_equal(nt, None)

    def test_get_parent_notification_type_no_parent(self):
        project = ProjectFactory()
        nt = utils.get_parent_notification_type(project._id, 'comments', self.user)
        assert_equal(nt, None)

    def test_get_parent_notification_type_handles_user_id(self):
        nt = utils.get_parent_notification_type(self.user._id, 'comments', self.user)
        assert_equal(nt, None)

    def test_format_data_project_settings(self):
        data = utils.format_data(self.user, [self.project._id])
        expected = [
            {
                'node': {
                    'id': self.project._id,
                    'title': self.project.title,
                    'url': self.project.url,
                },
                'kind': 'folder',
                'children': [
                    {
                        'event': {
                            'title': 'comments',
                            'description': SUBSCRIPTIONS_AVAILABLE['comments'],
                            'notificationType': 'email_transactional',
                            'parent_notification_type': None
                        },

                        'kind': 'event',
                        'children': []
                    },
                    {
                        'node': {
                            'id': self.node._id,
                            'title': self.node.title,
                            'url': self.node.url,
                        },

                        'kind': 'node',
                        'children': [
                            {
                                'event': {
                                    'title': 'comments',
                                    'description': SUBSCRIPTIONS_AVAILABLE['comments'],
                                    'notificationType': 'email_transactional',
                                    'parent_notification_type': 'email_transactional'
                                },
                                'kind': 'event',
                                'children': [],
                            }
                        ]
                    }
                ]
            }
        ]
        assert_equal(data, expected)

    def test_format_data_node_settings(self):
        data = utils.format_data(self.user, [self.node._id])
        expected = [{
            'node': {
                'id': self.node._id,
                'title': self.node.title,
                'url': self.node.url,
            },
            'kind': 'node',
            'children': [
                {
                    'event': {
                        'title': 'comments',
                        'description': SUBSCRIPTIONS_AVAILABLE['comments'],
                        'notificationType': 'email_transactional',
                        'parent_notification_type': 'email_transactional'
                    },
                    'kind': 'event',
                    'children': [],
                }
            ]
        }]
        assert_equal(data, expected)

    def test_format_includes_admin_view_only_component_subscriptions(self):
        """ Test private components in which parent project admins are not contributors still appear in their
            notifications settings.
        """
        node = NodeFactory(project=self.project)
        data = utils.format_data(self.user, [self.project._id])
        expected = [
            {
                'node': {
                    'id': self.project._id,
                    'title': self.project.title,
                    'url': self.project.url,
                },
                'kind': 'folder',
                'children': [
                    {
                        'event': {
                            'title': 'comments',
                            'description': SUBSCRIPTIONS_AVAILABLE['comments'],
                            'notificationType': 'email_transactional',
                            'parent_notification_type': None
                        },
                        'kind': 'event',
                        'children': []
                    },
                    {
                        'node': {
                            'id': self.node._id,
                            'title': self.node.title,
                            'url': self.node.url,
                        },

                        'kind': 'node',
                        'children': [
                            {
                                'event': {
                                    'title': 'comments',
                                    'description': SUBSCRIPTIONS_AVAILABLE['comments'],
                                    'notificationType': 'email_transactional',
                                    'parent_notification_type': 'email_transactional'
                                },
                                'kind': 'event',
                                'children': [],
                            }
                        ]
                    },
                    {
                        'node': {
                            'id': node._id,
                            'title': node.title,
                            'url': node.url,
                        },

                        'kind': 'node',
                        'children': [
                            {
                                'event': {
                                    'title': 'comments',
                                    'description': SUBSCRIPTIONS_AVAILABLE['comments'],
                                    'notificationType': 'adopt_parent',
                                    'parent_notification_type': 'email_transactional'
                                },
                                'kind': 'event',
                                'children': [],
                            }
                        ]
                    }
                ]
            }
        ]

        assert_equal(data, expected)

    def test_format_data_excludes_pointers(self):
        project = ProjectFactory()
        subscription = SubscriptionFactory(
            _id=project._id + '_comments',
            owner=project,
            event_name='comments'
        )
        subscription.email_transactional.append(project.creator)
        subscription.save()
        pointed = ProjectFactory()
        project.add_pointer(pointed, Auth(project.creator))
        project.save()
        configured_project_ids = utils.get_configured_projects(project.creator)
        data = utils.format_data(project.creator, configured_project_ids)
        expected = [{
            'node': {
                'id': project._id,
                'title': project.title,
                'url': project.url,
            },
            'kind': 'folder',
            'children': [
                {
                    'event': {
                        'title': 'comments',
                        'description': SUBSCRIPTIONS_AVAILABLE['comments'],
                        'notificationType': 'email_transactional',
                        'parent_notification_type': None
                    },
                    'kind': 'event',
                    'children': [],
                }
            ]
        }]
        assert_equal(data, expected)

    def test_format_data_user_subscriptions_includes_private_parent_if_configured_children(self):
        private_project = ProjectFactory()
        node = NodeFactory(project=private_project)
        node_subscription = SubscriptionFactory(
            _id=node._id + '_comments',
            owner=node,
            event_name='comments'
        )
        node_subscription.email_transactional.append(node.creator)
        node_subscription.save()
        configured_project_ids = utils.get_configured_projects(node.creator)
        data = utils.format_data(node.creator, configured_project_ids)
        expected = [
            {
                'node': {
                    'id': private_project._id,
                    'title': 'Private Project',
                    'url': '',
                },
                'kind': 'folder',
                'children': [
                    {
                        'node': {
                            'id': node._id,
                            'title': node.title,
                            'url': node.url,
                        },

                        'kind': 'folder',
                        'children': [
                            {
                                'event': {
                                    'title': 'comments',
                                    'description': SUBSCRIPTIONS_AVAILABLE['comments'],
                                    'notificationType': 'email_transactional',
                                    'parent_notification_type': None
                                },
                                'kind': 'event',
                                'children': [],
                            }
                        ]
                    }
                ]
            }
        ]
        assert_equal(data, expected)

    def test_format_user_subscriptions(self):
        data = utils.format_user_subscriptions(self.user, [])
        expected = [{
                    'event': {
                        'title': 'comment_replies',
                        'description': USER_SUBSCRIPTIONS_AVAILABLE['comment_replies'],
                        'notificationType': 'email_transactional',
                        'parent_notification_type': None
                    },
                    'kind': 'event',
                    'children': [],
                    }]
        assert_equal(data, expected)

    def test_format_data_user_settings(self):
        data = utils.format_user_and_project_subscriptions(self.user)
        expected = [
            {
                'node': {
                    'id': self.user._id,
                    'title': 'User Notifications'
            },
                'kind': 'heading',
                'children': utils.format_user_subscriptions(self.user, [])
            },
            {
                'node': {
                    'id': '',
                    'title': 'Project Notifications'
                },
                'kind': 'heading',
                'children': utils.format_data(self.user, utils.get_configured_projects(self.user))
            }]

        assert_equal(data, expected)

    def test_serialize_user_level_event(self):
        user_subscriptions = utils.get_all_user_subscriptions(self.user)
        data = utils.serialize_event(self.user, 'comment_replies', USER_SUBSCRIPTIONS_AVAILABLE, user_subscriptions)
        expected = {
            'event': {
                'title': 'comment_replies',
                'description': USER_SUBSCRIPTIONS_AVAILABLE['comment_replies'],
                'notificationType': 'email_transactional',
                'parent_notification_type': None
            },
            'kind': 'event',
            'children': []
                }
        assert_equal(data, expected)

    def test_serialize_node_level_event(self):
        node_subscriptions = utils.get_all_node_subscriptions(self.user, self.node)
        data = utils.serialize_event(self.user, 'comments', SUBSCRIPTIONS_AVAILABLE, node_subscriptions, self.node)
        expected = {
            'event': {
                'title': 'comments',
                'description': SUBSCRIPTIONS_AVAILABLE['comments'],
                'notificationType': 'email_transactional',
                'parent_notification_type': 'email_transactional'
            },
            'kind': 'event',
            'children': [],
        }
        assert_equal(data, expected)

    def test_serialize_node_level_event_that_adopts_parent_settings(self):
        user = UserFactory()
        self.project.add_contributor(contributor=user, permissions=['read'])
        self.project.save()
        self.project_subscription.email_transactional.append(user)
        self.project_subscription.save()
        self.node.add_contributor(contributor=user, permissions=['read'])
        self.node.save()
        node_subscriptions = utils.get_all_node_subscriptions(user, self.node)
        data = utils.serialize_event(user, 'comments', SUBSCRIPTIONS_AVAILABLE, node_subscriptions, self.node)
        expected = {
            'event': {
                'title': 'comments',
                'description': SUBSCRIPTIONS_AVAILABLE['comments'],
                'notificationType': 'adopt_parent',
                'parent_notification_type': 'email_transactional'
            },
            'kind': 'event',
            'children': [],
        }
        assert_equal(data, expected)


class TestNotificationsDict(OsfTestCase):
    def test_notifications_dict_add_message_returns_proper_format(self):
        d = utils.NotificationsDict()
        message = {
            'message': 'Freddie commented on your project',
            'timestamp': datetime.datetime.utcnow().replace(tzinfo=pytz.utc)
        }
        message2 = {
            'message': 'Mercury commented on your component',
            'timestamp':datetime.datetime.utcnow().replace(tzinfo=pytz.utc)
        }

        d.add_message(['project'], message)
        d.add_message(['project', 'node'], message2)

        expected = {
            'messages': [],
            'children': collections.defaultdict(
                utils.NotificationsDict, {
                    'project': {
                        'messages': [message],
                        'children': collections.defaultdict(utils.NotificationsDict, {
                            'node': {
                                'messages': [message2],
                                'children': collections.defaultdict(utils.NotificationsDict, {})
                            }
                        })
                    }
                }
            )}
        assert_equal(d, expected)


class TestSendEmails(OsfTestCase):
    def setUp(self):
        super(TestSendEmails, self).setUp()
        self.user = UserFactory()
        self.project = ProjectFactory()
        self.project_subscription = SubscriptionFactory(
            _id=self.project._id + '_' + 'comments',
            owner=self.project,
            event_name='comments'
        )
        self.project_subscription.save()
        self.project_subscription.email_transactional.append(self.project.creator)
        self.project_subscription.save()

        self.node = NodeFactory(project=self.project)
        self.node_subscription = SubscriptionFactory(
            _id=self.node._id + '_comments',
            owner=self.node,
            event_name='comments'
        )
        self.node_subscription.save()

    @mock.patch('website.notifications.emails.send')
    def test_notify_no_subscription(self, send):
        node = NodeFactory()
        emails.notify(node._id, 'comments')
        assert_false(send.called)

    @mock.patch('website.notifications.emails.send')
    def test_notify_no_subscribers(self, send):
        node = NodeFactory()
        node_subscription = SubscriptionFactory(
            _id=node._id + '_comments',
            owner=node,
            event_name='comments'
        )
        node_subscription.save()
        emails.notify(node._id, 'comments')
        assert_false(send.called)

    @mock.patch('website.notifications.emails.send')
    def test_notify_sends_with_correct_args(self, send):
        subscribed_users = getattr(self.project_subscription, 'email_transactional')
        emails.notify(self.project._id, 'comments')
        assert_true(send.called)
        send.assert_called_with(subscribed_users, 'email_transactional', self.project._id, 'comments')

    @mock.patch('website.notifications.emails.send')
    def test_notify_does_not_send_to_users_subscribed_to_none(self, send):
        node = NodeFactory()
        user = UserFactory()
        node_subscription = SubscriptionFactory(
            _id=node._id + '_comments',
            owner=node,
            event_name='comments'
        )
        node_subscription.save()
        node_subscription.none.append(user)
        node_subscription.save()
        emails.notify(node._id, 'comments')
        assert_false(send.called)

    @mock.patch('website.notifications.emails.send')
    def test_notify_sends_comment_reply_event_if_comment_is_reply(self, mock_send):
        user = UserFactory()
        sent_subscribers = emails.notify(self.project._id, 'comments', target_user=user)
        mock_send.assert_called_with([self.project.creator._id], 'email_transactional', self.project._id, 'comment_replies', target_user=user)

    # @mock.patch('website.notifications.emails.notify')
    @mock.patch('website.project.views.comment.notify')
    def test_check_user_comment_reply_subscription_if_email_not_sent_to_target_user(self, mock_notify):
        # user subscribed to comment replies
        user = UserFactory()
        user_subscription = SubscriptionFactory(
            _id=user._id + '_comments',
            owner=user,
            event_name='comment_replies'
        )
        user_subscription.email_transactional.append(user)
        user_subscription.save()

        # user is not subscribed to project comment notifications
        project = ProjectFactory()

        # reply to user
        target = CommentFactory(node=project, user=user)
        content = 'hammer to fall'

        # auth=project.creator.auth
        url = project.api_url + 'comment/'
        self.app.post_json(
            url,
            {
                'content': content,
                'isPublic': 'public',
                'target': target._id

            },
            auth=project.creator.auth
        )
        assert_true(mock_notify.called)
        assert_equal(mock_notify.call_count, 2)

    @mock.patch('website.notifications.emails.send')
    def test_check_parent(self, send):
        emails.check_parent(self.node._id, 'comments', [])
        assert_true(send.called)
        send.assert_called_with([self.project.creator._id], 'email_transactional', self.node._id, 'comments')

    @mock.patch('website.notifications.emails.send')
    def test_check_parent_does_not_send_if_notification_type_is_none(self, mock_send):
        project = ProjectFactory()
        project_subscription = SubscriptionFactory(
            _id=project._id,
            owner=project,
            event_name='comments'
        )
        project_subscription.save()
        project_subscription.none.append(project.creator)
        project_subscription.save()
        node = NodeFactory(project=project)
        emails.check_parent(node._id, 'comments', [])
        assert_false(mock_send.called)

    @mock.patch('website.notifications.emails.send')
    def test_check_parent_sends_to_subscribers_with_admin_read_access_to_component(self, mock_send):
        # Admin user is subscribed to receive emails on the parent project
        project = ProjectFactory()
        project_subscription = SubscriptionFactory(
            _id=project._id + '_comments',
            owner=project,
            event_name='comments'
        )
        project_subscription.save()
        project_subscription.email_transactional.append(project.creator)
        project_subscription.save()

        # User has admin read-only access to the component
        # Default is to adopt parent project settings
        node = NodeFactory(project=project)
        node_subscription = SubscriptionFactory(
            _id=node._id + '_comments',
            owner=node,
            event_name='comments'
        )
        node_subscription.save()

        # Assert that user receives an email when someone comments on the component
        emails.check_parent(node._id, 'comments', [])
        assert_true(mock_send.called)

    @mock.patch('website.notifications.emails.send')
    def test_check_parent_does_not_send_to_subscribers_without_access_to_component(self, mock_send):
        # Non-admin user is subscribed to receive emails on the parent project
        user = UserFactory()
        project = ProjectFactory()
        project.add_contributor(contributor=user, permissions=['read'])
        project_subscription = SubscriptionFactory(
            _id=project._id + '_comments',
            owner=project,
            event_name='comments'
        )
        project_subscription.save()
        project_subscription.email_transactional.append(user)
        project_subscription.save()

        # User does not have access to the component
        node = NodeFactory(project=project)
        node_subscription = SubscriptionFactory(
            _id=node._id + '_comments',
            owner=node,
            event_name='comments'
        )
        node_subscription.save()

        # Assert that user does not receive an email when someone comments on the component
        emails.check_parent(node._id, 'comments', [])
        assert_false(mock_send.called)


    # @mock.patch('website.notifications.emails.email_transactional')
    # def test_send_calls_correct_mail_function(self, email_transactional):
    #     emails.send([self.user], 'email_transactional', self.project._id, 'comments',
    #                 nodeType='project',
    #                 timestamp=datetime.datetime.utcnow(),
    #                 commenter=self.project.creator,
    #                 gravatar_url=self.user.gravatar_url,
    #                 content='',
    #                 parent_comment='',
    #                 title=self.project.title,
    #                 url=self.project.absolute_url
    #     )
    #     assert_true(email_transactional.called)

    @mock.patch('website.mails.send_mail')
    def test_send_email_transactional(self, send_mail):
        # assert that send_mail is called with the correct person & args
        subscribed_users = [self.user._id]
        timestamp = datetime.datetime.utcnow().replace(tzinfo=pytz.utc)

        emails.email_transactional(
            subscribed_users, self.project._id, 'comments',
            nodeType='project',
            timestamp=timestamp,
            commenter=self.project.creator,
            gravatar_url=self.user.gravatar_url,
            content='',
            parent_comment='',
            title=self.project.title,
            node_id=self.project._id,
            url=self.project.absolute_url
        )
        subject = Template(emails.email_templates['comments']['subject']).render(
            nodeType='project',
            timestamp=timestamp,
            commenter=self.project.creator,
            gravatar_url=self.user.gravatar_url,
            content='',
            parent_comment='',
            title=self.project.title,
            url=self.project.absolute_url)
        message = mails.render_message(
            'comments.html.mako',
            nodeType='project',
            timestamp=timestamp,
            commenter=self.project.creator,
            gravatar_url=self.user.gravatar_url,
            content='',
            parent_comment='',
            title=self.project.title,
            url=self.project.absolute_url,
            localized_timestamp=emails.localize_timestamp(timestamp, self.user))

        assert_true(send_mail.called)
        send_mail.assert_called_with(
            to_addr=self.user.username,
            mail=mails.TRANSACTIONAL,
            mimetype='html',
            name=self.user.fullname,
            node_title=self.project.title,
            node_id=self.project._id,
            subject=subject,
            message=message,
            url=self.project.absolute_url + 'settings/'
        )

    def test_send_email_digest_creates_digest_notification(self):
        subscribed_users = [self.user]
        digest_count_before = DigestNotification.find().count()
        emails.email_digest(subscribed_users, self.project._id, 'comments',
                            nodeType='project',
                            timestamp=datetime.datetime.utcnow().replace(tzinfo=pytz.utc),
                            commenter=self.project.creator,
                            gravatar_url=self.user.gravatar_url,
                            content='',
                            parent_comment='',
                            title=self.project.title,
                            url=self.project.absolute_url
        )
        digest_count = DigestNotification.find().count()
        assert_equal((digest_count - digest_count_before), 1)

    def test_send_email_digest_not_created_for_user_performed_actions(self):
        subscribed_users = [self.user]
        digest_count_before = DigestNotification.find().count()
        emails.email_digest(subscribed_users, self.project._id, 'comments',
                            nodeType='project',
                            timestamp=datetime.datetime.utcnow().replace(tzinfo=pytz.utc),
                            commenter=self.user,
                            gravatar_url=self.user.gravatar_url,
                            content='',
                            parent_comment='',
                            title=self.project.title,
                            url=self.project.absolute_url
        )
        digest_count = DigestNotification.find().count()
        assert_equal(digest_count_before, digest_count)

    def test_get_settings_url_for_node(self):
        url = emails.get_settings_url(self.project._id, self.user)
        assert_equal(url, self.project.absolute_url + 'settings/')

    def test_get_settings_url_for_user(self):
        url = emails.get_settings_url(self.user._id, self.user)
        assert_equal(url, web_url_for('user_notifications', _absolute=True))

    def test_get_node_lineage(self):
        node_lineage = emails.get_node_lineage(self.node, [])
        assert_equal(node_lineage, [self.node._id, self.project._id])

    def test_localize_timestamp(self):
        timestamp = datetime.datetime.utcnow().replace(tzinfo=pytz.utc)
        self.user.timezone = "America/New_York"
        self.user.save()
        localized_timestamp = emails.localize_timestamp(timestamp, self.user)
        expected_timestamp = timestamp.astimezone(pytz.timezone(self.user.timezone)).strftime('%c')
        assert_equal(localized_timestamp, expected_timestamp)
