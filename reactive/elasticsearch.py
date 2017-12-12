#!/usr/bin/env python3
# pylint: disable=c0111,c0103,c0301
import os
import subprocess as sp
from time import sleep


from charms.reactive import (
    clear_flag,
    endpoint_from_flag,
    is_flag_set,
    register_trigger,
    set_flag,
    when,
    when_any,
    when_not,
    hook,
)
from charmhelpers.core.hookenv import (
    application_version_set,
    config,
    log,
    open_port,
    status_set,
)
from charmhelpers.core.host import (
    chownr,
    service_restart,
    service_running,
    service_start,
    fstab_remove
)

from charmhelpers.core import unitdata

from charms.layer.elasticsearch import (
    # pylint: disable=E0611,E0401,C0412
    es_version,
    render_elasticsearch_file,
    DISCOVERY_FILE_PATH,
    DEFAULT_FILE_PATH,
    ELASTICSEARCH_YML_PATH,
    ES_PUBLIC_INGRESS_ADDRESS,
    ES_CLUSTER_INGRESS_ADDRESS,
    ES_CLUSTER_NAME,
    ES_NODE_TYPE,
    ES_HTTP_PORT,
    ES_TRANSPORT_PORT,
    ES_PLUGIN,
    NODE_TYPE_MAP
)


kv = unitdata.kv()


register_trigger(when='elasticsearch.version.set',
                 set_flag='elasticsearch.init.complete')


set_flag('elasticsearch.{}'.format(ES_NODE_TYPE))


@when_not('swap.removed')
def remove_swap():
    sp.call("swapoff -a".split())
    fstab_remove('none')
    set_flag('swap.removed')


@hook('start')
def set_elasticsearch_started_flag():
    set_flag('elasticsearch.juju.started')


@hook('data-storage-attached')
def set_storage_available_flag():
    set_flag('elasticsearch.storage.available')


@when('endpoint.member.joined')
def update_unitdata_kv():
    peers = endpoint_from_flag('endpoint.member.joined').all_units
    if len(peers) > 0 and \
       len([peer._data['private-address']
            for peer in peers if peer._data is not None]) > 0:
        kv.set('peer-nodes',
               [peer._data['private-address']
                for peer in peers if peer._data is not None])
        set_flag('render.elasticsearch.unicast-hosts')


# Utility Handlers
@when('elasticsearch.needs.restart')
def restart_elasticsearch():
    """Restart elasticsearch
    """
    service_restart('elasticsearch')
    clear_flag('elasticsearch.needs.restart')


@when('render.elasticsearch.unicast-hosts',
      'elasticsearch.discovery.plugin.available')
def update_discovery_file():
    """Update discovery-file
    """
    nodes = []

    if is_flag_set('elasticsearch.all') or \
       is_flag_set('elasticsearch.master'):
        nodes = kv.get('peer-nodes', [])
    else:
        nodes = kv.get('master-nodes', []) + kv.get('peer-nodes', [])

    render_elasticsearch_file(
        'unicast_hosts.txt.j2', DISCOVERY_FILE_PATH, {'nodes': nodes})

    clear_flag('render.elasticsearch.unicast-hosts')


@when('render.elasticsearch.yml')
def render_elasticsearch_conifg():
    """Render /etc/elasticsearch/elasticsearch.yml
    """
    ctxt = \
        {'cluster_name': config('cluster-name'),
         'cluster_network_ip': ES_CLUSTER_INGRESS_ADDRESS,
         'node_type': NODE_TYPE_MAP[config('node-type')],
         'custom_config': config('custom-config')}

    render_elasticsearch_file(
        'elasticsearch.yml.j2', ELASTICSEARCH_YML_PATH, ctxt)

    if not is_flag_set('elasticsearch.init.config.rendered'):
        set_flag('elasticsearch.init.config.rendered')
    set_flag('elasticsearch.needs.restart')
    clear_flag('render.elasticsearch.yml')


@when_any('apt.installed.elasticsearch',
          'deb.installed.elasticsearch')
# @when('elasticsearch.storage.available')
@when_not('elasticsearch.storage.prepared')
def prepare_data_dir():
    """This should be the first thing to run after elasticsearch
    is installed.
    """
    if not os.path.isdir('/srv/elasticsearch-data'):
        os.makedirs("/srv/elasticsearch-data", exist_ok=True)

    chownr(path='/srv/elasticsearch-data', owner='elasticsearch',
           group='elasticsearch', follow_links=True,
           chowntopdir=True)

    set_flag('elasticsearch.storage.prepared')


@when_any('apt.installed.elasticsearch',
          'deb.installed.elasticsearch')
@when_not('elasticsearch.defaults.available')
def render_elasticsearch_defaults():
    """This should be the first thing to run after elasticsearch
    is installed.
    """
    ctxt = {}
    if config('java-opts'):
        ctxt['java_opts'] = config('java-opts')

    render_elasticsearch_file(
        'elasticsearch.default.j2', DEFAULT_FILE_PATH, ctxt, 'root', 'root')

    set_flag('elasticsearch.defaults.available')
    status_set('active', "Elasticsearch defaults available")


@when_not('elasticsearch.discovery.plugin.available')
@when_any('deb.installed.elasticsearch', 'apt.installed.elasticsearch')
def install_file_based_discovery_plugin():
    """Install the file based discovery plugin
    """
    # TODO(jamesbeedy): REVISIT TO SUPPORT MORE DISCOVERY PLUGINS
    # Possibly this isn't the best location to do this - revisit
    if os.path.exists(ES_PLUGIN):
        sp.call("{} install discovery-file".format(ES_PLUGIN).split())
        set_flag('elasticsearch.discovery.plugin.available')
    else:
        log("BAD THINGS - elasticsearch-plugin not available")
        status_set('blocked',
                   "Cannot find elasticsearch plugin manager - "
                   "please debug {}".format(ES_PLUGIN))


@when_not('elasticsearch.init.running')
@when('elasticsearch.discovery.plugin.available',
      'elasticsearch.storage.prepared',
      'elasticsearch.defaults.available')
def ensure_elasticsearch_started():
    """Ensure elasticsearch is started
    """

    sp.call("systemctl daemon-reload".split())
    sp.call("systemctl enable elasticsearch.service".split())

    # If elasticsearch isn't running start it
    if not service_running('elasticsearch'):
        service_start('elasticsearch')
    # If elasticsearch is running restart it
    else:
        service_restart('elasticsearch')

    # Wait 100 seconds for elasticsearch to restart, then break out of the loop
    # and blocked wil be set below
    cnt = 0
    while not service_running('elasticsearch') and cnt < 100:
        status_set('waiting', 'Waiting for Elasticsearch to start')
        sleep(1)
        cnt += 1

    if service_running('elasticsearch'):
        set_flag('elasticsearch.init.running')
        status_set('active', 'Elasticsearch running')
    else:
        # If elasticsearch wont start, set blocked
        status_set('blocked',
                   "There are problems with elasticsearch, please debug")
        return


@when('elasticsearch.init.running')
@when_not('elasticsearch.version.set')
def get_set_elasticsearch_version():
    """Wait until we have the version to confirm
    elasticsearch has started
    """

    status_set('maintenance', 'Waiting for Elasticsearch to start')
    application_version_set(es_version())
    status_set('active', 'Elasticsearch running - init complete')
    set_flag('elasticsearch.version.set')


# Elasticsearch initialization should be complete at this point
# The following ops are all post init phase
@when('elasticsearch.init.complete')
@when_not('elasticsearch.transport.port.available')
def open_transport_port():
    """Open port 9300 for transport protocol

    This is a quick hack to make sure the correct ports are
    open following successful initialization.

    Possibly there is a smarter
    way to do this then just opening the port directly?

    /charms/layer/elasticsearch_security.py ?

    We don't need both 9200 and 9300 open to the public
    internet, ALMOST NEVER should this happen.
    """
    # TODO(jamesbeedy): Figure out a way forward here other then this.
    # or possibly this is ok, do I even need to open port 9300
    # if it is a best practice to not have es cross talk over wan? -
    # then we wouldn't even need to open the port if we just ensure
    # the readme states outright that the charm doesn't support
    # es <-> es traffic over the WAN.
    # for now, just open the transport port
    open_port(ES_TRANSPORT_PORT)
    set_flag('elasticsearch.transport.port.available')


# Node-Type ALL Handlers
@when('elasticsearch.transport.port.available',
      'elasticsearch.all',
      'elasticsearch.juju.started')
@when_not('elasticsearch.init.config.rendered')
def render_init_config_for_node_type_all():
    set_flag('render.elasticsearch.yml')


@when('elasticsearch.init.config.rendered', 'elasticsearch.all')
def node_type_all_init_complete():
    status_set('active',
               'Elasticsearch Running - {} nodes'.format(
                   len(kv.get('peer-nodes', [])) + 1))
    set_flag('elasticsearch.all.available')


# Node-Type Tribe/Ingest/Data Handlers
@when_any('elasticsearch.tribe',
          'elasticsearch.ingest',
          'elasticsearch.data')
@when('elasticsearch.init.complete')
@when_not('elasticsearch.master.acquired')
def block_until_master_relation():
    """Block non-master node types until we have a master relation
    """
    status_set('blocked',
               'Need relation to Elasticsearch master to continue')
    return


@when('elasticsearch.init.complete',
      'elasticsearch.master',
      'elasticsearch.juju.started')
@when_not('elasticsearch.min.masters.available')
def block_until_min_masters():
    """Block master node types from making further progress
    until we have >= config('min-master-count')
    """
    if not (len(kv.get('peer-nodes', [])) >= (config('min-master-count') - 1)):
        status_set('blocked',
                   'Need >= config("min-master-count") masters to continue')
        return
    else:
        set_flag('elasticsearch.min.masters.available')


@when_any('elasticsearch.min.masters.available',
          'elasticsearch.master.acquired')
@when('elasticsearch.transport.port.available')
@when_not('elasticsearch.init.config.rendered')
def render_elasticsearch_yml_init():
    set_flag('render.elasticsearch.yml')


@when('elasticsearch.init.config.rendered')
@when_not('elasticsearch.all')
def elasticsearch_node_available():
    if not service_running('elasticsearch'):
        cnt = 0
        while not service_running('elasticsearch') and cnt < 100:
            status_set('waiting',
                       "Waiting on Elasticsearch/{} to start".format(
                           ES_NODE_TYPE))
            cnt += 1
        # Blocked can't start elasticsearch
        status_set('blocked',
                   "Cannot start Elasticsearch/{} - please debug".format(
                       ES_NODE_TYPE))
        return
    else:
        status_set('active', "{} node - Ready".format(
            ES_NODE_TYPE.capitalize()))
        set_flag('elasticsearch.{}.available'.format(ES_NODE_TYPE))


# Client Relation
@when('endpoint.client.joined',
      'elasticsearch.{}.available'.format(ES_NODE_TYPE))
@when_not('juju.elasticsearch.client.joined')
def provide_client_relation_data():
    if ES_NODE_TYPE not in ['master', 'all']:
        log("SOMETHING BAD IS HAPPENING - wronge nodetype for client relation")
        status_set('blocked',
                   "Cannot make relation to master - "
                   "wrong node-typeforclient relation, please remove relation")
        return
    else:
        open_port(ES_HTTP_PORT)
        endpoint_from_flag('endpoint.client.joined').configure(
            ES_PUBLIC_INGRESS_ADDRESS, ES_HTTP_PORT, ES_CLUSTER_NAME)

    set_flag('juju.elasticsearch.client.joined')


# Non-Master Node Relation
@when('endpoint.require-master.available')
@when_not('juju.elasticsearch.require-master.joined')
def get_all_master_nodes():
    master_nodes = []

    for es in endpoint_from_flag(
       'endpoint.require-master.available').relation_data():
            master_nodes.append(es['host'])

    kv.set('master-nodes', master_nodes)

    set_flag('render.elasticsearch.unicast-hosts')
    set_flag('elasticsearch.master.acquired')
    set_flag('juju.elasticsearch.require-master.joined')


# Master Node Relation
@when('endpoint.provide-master.joined')
@when_not('juju.elasticsearch.provide-master.joined')
def provide_master_node_type_relation_data():
    if not ES_NODE_TYPE == 'master':
        log("SOMETHING BAD IS HAPPENING - wronge node type for relation")
        status_set('blocked',
                   "Cannot make relation to master - "
                   "wrong node-type for relation")
        return
    else:
        endpoint_from_flag('endpoint.provide-master.joined').configure(
            ES_CLUSTER_INGRESS_ADDRESS, ES_TRANSPORT_PORT, ES_CLUSTER_NAME)

    set_flag('juju.elasticsearch.provide-master.joined')


#@when('config.changed.min-master-count',
#      'elasticsearch.juju.started')
#def clear_min_master_flag():
#    clear_flag('elasticsearch.min.masters.available')
