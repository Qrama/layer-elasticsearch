#!/usr/bin/env python3
# pylint: disable=c0111,c0103,c0301
import json
from time import sleep
import subprocess as sp
import requests
from charmhelpers.core.hookenv import status_set, log


class ElasticsearchError(Exception):
    """Base class for exceptions in this module."""
    pass


class ElasticsearchApiError(ElasticsearchError):
    def __init__(self, message):
        self.message = message


def is_container():
    """Return True if system is running inside a container"""
    virt_type = sp.check_output('systemd-detect-virt').decode().strip()
    if virt_type == 'lxc':
        return True
    else:
        return False


def es_version():
    """Return elasticsearch version
    """

    # Poll until elasticsearch has started, otherwise the curl
    # to get the version will error out
    status_code = 0
    counter = 0
    try:
        while status_code != 200 and counter < 100:
            try:
                counter += 1
                log("Polling for elasticsearch api: %d" % counter)
                req = requests.get('http://localhost:9200')
                status_code = req.status_code
                es_curl_data = req.text.strip()
                json_acceptable_data = \
                    es_curl_data.replace("\n", "").replace("'", "\"")
                return json.loads(json_acceptable_data)['version']['number']
            except requests.exceptions.ConnectionError:
                sleep(1)
        log("Elasticsearch needs debugging, cannot access api")
        status_set('blocked', "Cannot access elasticsearch api")
        raise ElasticsearchApiError("%d seconds waiting for es api to no avail" % counter)
    except ElasticsearchApiError as e:
        log(e.message)
