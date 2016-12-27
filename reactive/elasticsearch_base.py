#!/usr/bin/env python3
# pylint: disable=c0111,c0103,c0301
import os
import subprocess as sp

from charms.reactive import (
    when,
    when_not,
    set_state
)
from charmhelpers.core.hookenv import (
    status_set,
    application_version_set
)
from charmhelpers.core.host import (
    service_running,
    service_start
)
from charms.layer.elasticsearch_base import (
    # pylint: disable=E0611,E0401,C0412
    is_container,
    es_version
)


@when_not('java.ready')
def java_waiting_status():
    status_set('blocked', 'Need relation to java providing application')


@when('java.ready')
@when_not('elasticsearch.installed')
def install_elasticsearch(java):
    """Check for container, install elasticsearch
    """
    # Workaround for container installs is to set
    # ES_SKIP_SET_KERNEL_PARAMETERS if in container
    # so kernel files will not need to be modified on
    # elasticsearch install. See
    # https://github.com/elastic/elasticsearch/commit/32df032c5944326e351a7910a877d1992563f791
    if is_container():
        os.environ['ES_SKIP_SET_KERNEL_PARAMETERS'] = 'true'
    sp.call(['apt', 'install', 'elasticsearch', '-y',
             '--allow-unauthenticated'], shell=False)
    set_state('elasticsearch.installed')


@when('elasticsearch.installed')
@when_not('elasticsearch.running')
def ensure_elasticsearch_running():
    """Ensure elasticsearch is started
    """

    # If elasticsearch isn't running start it
    if not service_running('elasticsearch'):
        service_start('elasticsearch')
    # If elasticsearch starts, were g2g, else block
    if service_running('elasticsearch'):
        set_state('elasticsearch.running')
        status_set('active', 'Ready')
    else:
        # If elasticsearch wont start, set blocked
        set_state('elasticsearch.problems')


@when('elasticsearch.installed', 'elasticsearch.running')
@when_not('elasticsearch.version.set')
def get_set_elasticsearch_version():
    """Wait until we have the version to confirm
    elasticsearch has started
    """

    status_set('maintenance', 'Waiting for Elasticsearch to start')
    application_version_set(es_version())
    status_set('active', 'Elasticsearch started')
    set_state('elasticsearch.version.set')


@when('elasticsearch.version.set')
@when_not('elasticsearch.base.available')
def set_elasticsearch_base_available():
    """Set active status, and 'elasticsearch.base.available'
    state
    """
    status_set('active', 'Elasticsearch base available')
    set_state('elasticsearch.base.available')


@when('elasticsearch.problems')
def blocked_due_to_problems():
    """Set blocked status if we encounter issues
    """
    status_set('blocked',
               "There are problems with elasticsearch, please debug")
