# Overview

Elasticsearch is a flexible and powerful open source, distributed, real-time
search and analytics engine. Architected from the ground up for use in
distributed environments where reliability and scalability are must haves,
Elasticsearch gives you the ability to move easily beyond simple full-text
search. Through its robust set of APIs and query DSLs, plus clients for the
most popular programming languages, Elasticsearch delivers on the near
limitless promises of search technology.

Excerpt from [elasticsearch.org](http://www.elasticsearch.org/overview/ "Elasticsearch Overview")

# Usage
This charm can be used to deploy Elasticsearch in any way that is suppored by upstream Elasticsearch, and then some.

The charm configuration exposes an option called `node-type`, which is used by the charm to know what type of 
Elasticsearch node to configure. In order to orchestrate this charm to facilitate more complex deployments,
you must configure the node-type on a per application basis.

The options for the `node-type` config can be explained as follows:
* `all` - The node will assume all roles of Elasticsearch, there will be no difference in configuration from one node to the next.
* `master` - The node will assume the 'master' node-type. Master nodes will wait for the number of peers to be >= the charm configuration
option `minn-master-count` (this defaults to 1) before bootstrapping the cluster. 
* `tribe` - The node will assume the 'tribe' node-type. Tribe nodes will wait until they have a relation to the master before
joining the cluster.
* `data` - The node will assume the 'data' node-type. Data nodes will wait until they have a relation to the master before
joining the cluster.
* `ingest` - The node will assume the 'ingest' node-type. Ingest nodes will wait until they have a relation to the master before
joining the cluster.

### Basic (node-type='all')
Deploying this charm with the defaults will get you Elasticsearch installed from the elastic.co apt sources, and an all-in-one node-type, where
each unit is every node type.

For example:

```bash
juju deploy elasticsearch -n 3
```

The above command would deploy 3 elasticsearch nodes, of which all are master, data, ingest, and tribe.
This functionality mirrors that of the legacy Juju Elasticsearch charm, you can deploy and scale a cluster without worrying about node-types
because every node is all node-types.

### Desparate Node Types
The extended functionality of this charm lends itself to the configuration of Elasticsearch clusters with non-uniform node-types.

For example:
```yaml
# config.yaml
es-master:
  node-type: "master"
es-data:
  node-type: "data"
es-tribe:
  node-type: "tribe"
es-ingest:
  node-type: "ingest"
```
```bash
# Deploy 3 units of each node-type
juju deploy elasticsearch es-master -n 3 --config config.yaml

juju deploy elasticsearch es-data -n 3 --config config.yaml

juju deploy elasticsearch es-ingest -n 3 --config config.yaml

juju deploy elasticsearch es-tribe -n 3 --config config.yaml

# Make the relations between the components of the cluster
juju relate es-master:master-data es-data:data-node
juju relate es-master:master-node es-data:master

juju relate es-master:master-ingest es-ingest:ingest-node
juju relate es-master:master-node es-ingest:master

juju relate es-master:master-tribe es-tribe:tribe-node
juju relate es-master:master-node es-tribe:master
```

# Managed Configuration MGMT
This charm manages three files primarily, they are:
* `/etc/default/elasticsearch`
* `/etc/elasticsearch/elasticsearch.yml`
* `/etc/elasticsearch/discovery-file/unicast_hosts.txt`

If you wish to make a customization to any of the aforementioned files, please
do so through the charm configuration options.

Only file based discovery is currently supported (pull requests welcome!).

# Copyright
* AGPLv3 (see `copyright` file in this directory)

# Contact Information

* James Beedy <jamesbeedy@gmail.com>
* Sebastien Pattyn <sebastien.pattyn@gmail.com>

## Elasticsearch
- [Elasticsearch website](http://www.elasticsearch.org/)
