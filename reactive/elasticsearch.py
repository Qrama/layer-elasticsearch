#!/usr/bin/env python3
# pylint: disable=c0111,c0103,c0301
import os
import subprocess as sp
from time import sleep


from charms.reactive import (
    clear_flag,
    context,
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
    service_restart,
    service_running,
    service_start,
)

from charmhelpers.core import unitdata

from charms.layer.elasticsearch import (
    # pylint: disable=E0611,E0401,C0412
    chown,
    es_version,
    render_elasticsearch_file,
    DISCOVERY_FILE_PATH,
    DEFAULT_FILE_PATH,
    ELASTICSEARCH_YML_PATH,
    ES_PUBLIC_INGRESS_ADDRESS,
    ES_CLUSTER_INGRESS_ADDRESS,
    ES_NODE_TYPE,
    ES_HTTP_PORT,
    ES_TRANSPORT_PORT,
    ES_PLUGIN,
    NODE_TYPE_MAP
)


kv = unitdata.kv()


register_trigger(when='elasticsearch.version.set',
                 set_flag='elasticsearch.init.complete')

register_trigger(when='elasticsearch.grafana.available',
                 clear_flag='elasticsearch.grafana.unavailable')

register_trigger(when='elasticsearch.grafana.unavailable',
                 clear_flag='elasticsearch.grafana.available')

set_flag('elasticsearch.{}'.format(ES_NODE_TYPE))


@hook('start')
def update_peers_on_start():
    set_flag('elasticsearch.juju.started')


@hook('data-storage-attached')
def set_storage_available_flag():
    set_flag('elasticsearch.storage.available')


# Peer Relation Handlers
@when('endpoint.member.cluster.departed')
def elasticsearch_member_departed():
    set_flag('update.peers')
    clear_flag('endpoint.member.cluster.departed')


@when('endpoint.member.cluster.joined')
def elasticsearch_member_joined():
    set_flag('update.peers')
    clear_flag('endpoint.member.cluster.joined')


@when('update.peers')
def update_unitdata_kv():
    peers = context.endpoints.member.peer_info()
    if len(peers) > 0:
        # Not sure if this will work correctly with network-get/spaces
        # TODO(jamesbeedy): figure this out (possibly talk to cory_fu in #juju)
        kv.set('peer-nodes', [peer._data['private-address'] for peer in peers])
    else:
        kv.set('peer-nodes', [])
    set_flag('render.elasticsearch.unicast-hosts')
    clear_flag('update.peers')


# Utility Handlers
@when('elasticsearch.needs.restart')
def restart_elasticsearch():
    """Restart elasticsearch
    """
    service_restart('elasticsearch')
    clear_flag('elasticsearch.needs.restart')


@when('render.elasticsearch.unicast-hosts')
def update_discovery_file():
    """Update discovery-file
    """
    nodes = []

    for node_type in ['data-nodes', 'ingest-nodes',
                      'tribe-nodes', 'peer-nodes', 'master-nodes']:
        if kv.get(node_type, ''):
            [nodes.append(node) for node in kv.get(node_type)]

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
@when('elasticsearch.storage.available')
@when_not('elasticsearch.storage.prepared')
def prepare_data_dir():
    """This should be the first thing to run after elasticsearch
    is installed.
    """
    if os.path.exists('/srv/elasticsearch-data'):
        chown(path='/srv/elasticsearch-data', user='elasticsearch',
              group='elasticsearch', recursive=True)
        set_flag('elasticsearch.storage.prepared')
    else:
        status_set('blocked', "DATA DIR NOT AVAILABLE _DEBUG")
        return

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

    # If elasticsearch isn't running start it
    if not service_running('elasticsearch'):
        service_start('elasticsearch')

    # If elasticsearch is running restart it
    if service_running('elasticsearch'):
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
def provide_client_relation_data():
    if ES_NODE_TYPE not in ['master', 'all']:
        log("SOMETHING BAD IS HAPPENING - wronge nodetype for client relation")
        status_set('blocked',
                   "Cannot make relation to master - "
                   "wrong node-typeforclient relation, please remove relation")
        return
    else:
        open_port(ES_HTTP_PORT)
        context.endpoints.client.configure(
            ES_PUBLIC_INGRESS_ADDRESS, ES_HTTP_PORT)
    clear_flag('endpoint.client.joined')


# NON-MASTER NODE Relations
@when('endpoint.data-node.joined')
def provide_data_node_type_relation_data():
    if not ES_NODE_TYPE == 'data':
        log("SOMETHING BAD IS HAPPENING - wronge node type for relation")
        status_set('blocked',
                   "Cannot make relation to master - "
                   "wrong node-type for relation")
        return
    else:
        context.endpoints.data_node.configure(
            ES_CLUSTER_INGRESS_ADDRESS, ES_HTTP_PORT)
    clear_flag('endpoint.data-node.joined')


@when('endpoint.ingest-node.joined')
def provide_ingest_node_type_relation_data():
    if not ES_NODE_TYPE == 'ingest':
        log("SOMETHING BAD IS HAPPENING - wronge node type for relation")
        status_set('blocked',
                   "Cannot make relation to master - "
                   "wrong node-type for relation")
        return
    else:
        context.endpoints.ingest_node.configure(
            ES_CLUSTER_INGRESS_ADDRESS, ES_HTTP_PORT)
    clear_flag('endpoint.ingest-node.joined')


@when('endpoint.tribe-node.joined')
def provide_tribe_node_type_relation_data():
    if not ES_NODE_TYPE == 'tribe':
        log("SOMETHING BAD IS HAPPENING - wronge node type for relation")
        status_set('blocked',
                   "Cannot make relation to master - "
                   "wrong node-type for relation")
        return
    else:
        context.endpoints.tribe_node.configure(
            ES_CLUSTER_INGRESS_ADDRESS, ES_HTTP_PORT)
    clear_flag('endpoint.tribe-node.joined')


@when('endpoint.master.host-port')
def get_all_master_nodes():
    master_nodes = []
    for master_node in context.endpoints.master.relation_data():
        master_nodes.append(master_node['host'])
    kv.set('master-nodes', master_nodes)

    set_flag('render.elasticsearch.unicast-hosts')
    set_flag('elasticsearch.master.acquired')
    clear_flag('endpoint.master.host-port')


# MASTER NODE Relations
@when('endpoint.master-data.host-port',
      'elasticsearch.min.masters.available')
def get_set_data_nodes():
    data_nodes = []
    for data_node in context.endpoints.master_data.relation_data():
        data_nodes.append(data_node['host'])
    kv.set('data-nodes', data_nodes)

    status_set('active',
               "Data node(s) acquired - {}".format(" ".join(data_nodes)))
    set_flag('render.elasticsearch.unicast-hosts')
    clear_flag('endpoint.master-data.host-port')


@when('endpoint.master-ingest.host-port',
      'elasticsearch.min.masters.available')
def get_set_ingest_nodes():
    ingest_nodes = []
    for ingest_node in context.endpoints.master_ingest.relation_data():
        ingest_nodes.append(ingest_node['host'])
    kv.set('ingest-nodes', ingest_nodes)

    status_set('active',
               "Ingest node(s) acquired - {}".format(" ".join(ingest_nodes)))
    set_flag('render.elasticsearch.unicast-hosts')
    clear_flag('endpoint.master-ingest.host-port')


@when('endpoint.master-tribe.host-port',
      'elasticsearch.min.masters.available')
def get_set_tribe_nodes():
    tribe_nodes = []
    for tribe_node in context.endpoints.master_tribe.relation_data():
        tribe_nodes.append(tribe_node['host'])
    kv.set('tribe-nodes', tribe_nodes)

    status_set('active',
               "Tribe node(s) acquired - {}".format(" ".join(tribe_nodes)))
    set_flag('render.elasticsearch.unicast-hosts')
    clear_flag('endpoint.master-tribe.host-port')


@when('endpoint.master-node.joined')
def provide_master_node_type_relation_data():
    if not ES_NODE_TYPE == 'master':
        log("SOMETHING BAD IS HAPPENING - wronge node type for relation")
        status_set('blocked',
                   "Cannot make relation to master - "
                   "wrong node-type for relation")
        return
    else:
        context.endpoints.master_node.configure(
            ES_CLUSTER_INGRESS_ADDRESS, ES_HTTP_PORT)
    clear_flag('endpoint.master-node.joined')


@when('config.changed.min-master-count',
      'elasticsearch.juju.started')
def clear_min_master_flag():
    clear_flag('elasticsearch.min.masters.available')


@when('elasticsearch.{}.available'.format(ES_NODE_TYPE),
      'leadership.is_leader',
      'grafana-source.available')
@when_any('elasticsearch.all',
          'elasticsearch.master')
@when_not('elasticsearch.grafana.available')
def provide_grafana_source(grafana):
    grafana.provide(
        source_type='elasticsearch',
        url_or_port='http://{}:{}'.format(
            ES_PUBLIC_INGRESS_ADDRESS, ES_HTTP_PORT),
        description='Juju generated source elasticsearch source',
        database='elasticsearch')
    status_set('active', 'Grafana joined')
    set_flag('elasticsearch.grafana.available')


@when_not('grafana-source.available',
          'elasticsearch.grafana.unavailable')
@when('elasticsearch.grafana.available')
def remove_grafana_source_flags():
    set_flag('elasticsearch.grafana.unavailable')
