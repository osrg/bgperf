#!/usr/bin/env python
#
# Copyright (C) 2015 Nippon Telegraph and Telephone Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import yaml
import toml
import glob
import os
from argparse import ArgumentParser
from fabric.api import local, settings

CONFIG_DIR = '/tmp/bgperf'
BR_NAME = 'bgperf_br'

conf = {}

class CmdBuffer(list):
    def __init__(self, delim='\n'):
        super(CmdBuffer, self).__init__()
        self.delim = delim

    def __lshift__(self, value):
        self.append(value)

    def __str__(self):
        return self.delim.join(self)


def run_gobgp(conf):
    config = {'Global': {'GlobalConfig': {'As': conf['target']['as'], 'RouterId': conf['target']['router-id'], 'Port': -1}}}
    for peer in conf['tester'].itervalues():
        n = {'NeighborConfig': {
            'NeighborAddress': peer['local-address'].split('/')[0],
            'PeerAs': peer['as'],
            },
            'Transport': {
                'TransportConfig': {
                    'LocalAddress': conf['target']['local-address'].split('/')[0],
                },
            },
            'RouteServer': {
                'RouteServerConfig': {
                    'RouteServerClient': True,
                },
            },
        }
        if 'Neighbors' not in config:
            config['Neighbors'] = {'NeighborList': []}
        config['Neighbors']['NeighborList'].append(n)

    with open('{0}/{1}'.format(CONFIG_DIR, 'gobgp.conf'), 'w') as f:
        f.write(toml.dumps(config))

    with settings(warn_only=True):
        local('pkill gobgpd')
    local('gobgpd -f {0}/{1} > {0}/target.log 2>&1 &'.format(CONFIG_DIR, 'gobgp.conf'))


def run_target(conf):
    local('ip a add {0} dev {1}'.format(conf['target']['local-address'], BR_NAME))
    if conf['target']['type'] == 'gobgp':
        run_gobgp(conf)
    else:
        raise Exception('unsupported target type')


def run_tester(conf):
    for peer in conf['tester'].itervalues():
        local('ip a add {0} dev {1}'.format(peer['local-address'], BR_NAME))
        cmd = CmdBuffer()
        cmd << 'neighbor {0} {{'.format(conf['target']['local-address'].split('/')[0])
        cmd << '    router-id {0};'.format(peer['router-id'])
        cmd << '    local-address {0};'.format(peer['local-address'].split('/')[0])
        cmd << '    local-as {0};'.format(peer['as'])
        cmd << '    peer-as {0};'.format(conf['target']['as'])
        if len(peer['paths']) > 0:
            cmd << '    static {'
            for path in peer['paths']:
                cmd << '        route {0} next-hop {1};'.format(path, peer['local-address'].split('/')[0])
            cmd << '    }'
        cmd << '}'

        with open('{0}/{1}.conf'.format(CONFIG_DIR, peer['router-id']), 'w') as f:
            f.write(str(cmd))

        cmd = CmdBuffer(' ')
        cmd << 'env exabgp.log.destination={0}/{1}.log'.format(CONFIG_DIR, peer['router-id'])
        cmd << 'exabgp.daemon.daemonize=true'
        cmd << 'exabgp.daemon.user=root'
        cmd << 'exabgp.daemon.pid={0}/{1}.pid'.format(CONFIG_DIR, peer['router-id'])
        cmd << 'exabgp {0}/{1}.conf'.format(CONFIG_DIR, peer['router-id'])
        local(str(cmd))


def main():
    parser = ArgumentParser(description='BGP performance measuring tool')
    parser.add_argument('-f', '--file', default='bgperf.yml', type=str)
    args = parser.parse_args()

    with settings(warn_only=True):
        for path in glob.glob('{0}/*.pid'.format(CONFIG_DIR)):
            with open(path, 'r') as f:
                try:
                    pid = f.readline().strip()
                    print 'kill', pid
                    os.kill(int(pid), signal.SIGKILL)
                except:
                    print 'failed'
                    pass
            os.remove(path)
        for path in glob.glob('{0}/*.log'.format(CONFIG_DIR)):
            os.remove(path)
        local('ip li del dev {0}'.format(BR_NAME))
    local('ip li add {0} type bridge'.format(BR_NAME))
    local('ip li set up dev {0}'.format(BR_NAME))
    local('mkdir -p {0}'.format(CONFIG_DIR))

    with open(args.file) as f:
        conf = yaml.load(f)
        run_target(conf)
        run_tester(conf)
