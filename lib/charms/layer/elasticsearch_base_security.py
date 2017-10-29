import subprocess as sp
from jujubigdata import utils

from charmhelpers.core.hookenv import config


def init_fw():
    # this value has te be changed to set ufw rules
    utils.re_edit_in_place('/etc/default/ufw', {
        r'IPV6=yes': 'IPV6=no',
    })
    if config('firewall_enabled'):
        sp.check_call(['ufw', 'allow', '22'])
        sp.check_output(['ufw', 'enable'], input='y\n',
                        universal_newlines=True)
    else:
        sp.check_output(['ufw', 'disable'])


def add_fw_exception(host_ip):
    sp.check_call([
        'ufw', 'allow', 'proto', 'tcp', 'from', host_ip,
        'to', 'any', 'port', '9200'])


def rm_fw_exception(host_ip):
    sp.check_call([
        'ufw', 'delete', 'allow', 'proto', 'tcp', 'from', host_ip,
        'to', 'any', 'port', '9200'])
