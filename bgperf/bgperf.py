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
    config = {'global': {
                'config': {
                    'as': conf['target']['as'],
                    'router-id': conf['target']['router-id']
                },
            }}
    for peer in conf['tester'].itervalues():
        n = {'config': {
                'neighbor-address': peer['local-address'].split('/')[0],
                'peer-as': peer['as']
                },
             'transport': {
                'config': {
                    'local-address': conf['target']['local-address'].split('/')[0],
                },
            },
            'route-server': {
                'config': {
                    'route-server-client': True,
                },
            },
        }
        if 'neighbors' not in config:
            config['neighbors'] = []
        config['neighbors'].append(n)

    with open('{0}/{1}'.format(CONFIG_DIR, 'gobgpd.conf'), 'w') as f:
        f.write(toml.dumps(config))


    if 'docker' in conf['target']:
        DOCKER_SHARED_DIR = '/root/shared_volume'
        name = 'gobgp'
        if 'name' in conf['target']['docker']:
            name = conf['target']['docker']['name']

        image = 'osrg/gobgp'
        if 'image' in conf['target']['docker']:
            image = conf['target']['docker']['image']

        c = CmdBuffer(' ')
        c << 'docker run --privileged=true'
        c << '-v {0}:{1}'.format(CONFIG_DIR, DOCKER_SHARED_DIR)
        c << '--name {0} -id {1}'.format(name, image)
        with settings(warn_only=True):
            local('docker rm -f {0}'.format(name))
        local(str(c))
        local('docker exec {0} ip li set up dev lo'.format(name))
        local('pipework {0} {1} {2}'.format(BR_NAME, name, conf['target']['local-address']))
        def _start_gobgp():
            c = CmdBuffer()
            c << '#!/bin/bash'
            c << '/go/bin/gobgpd -f {0}/gobgpd.conf -l {1} > ' \
                 '{0}/gobgpd.log 2>&1'.format(DOCKER_SHARED_DIR, 'debug')

            cmd = 'echo "{0:s}" > {1}/start.sh'.format(c, CONFIG_DIR)
            local(cmd, capture=True)
            cmd = "chmod 755 {0}/start.sh".format(CONFIG_DIR)
            local(cmd, capture=True)
            local("docker exec -d {0} {1}/start.sh".format(name, DOCKER_SHARED_DIR))
        _start_gobgp()
    else:
        local('ip a add {0} dev {1}'.format(conf['target']['local-address'], BR_NAME))
        with settings(warn_only=True):
            local('pkill gobgpd')
        local('gobgpd -f {0}/{1} > {0}/target.log 2>&1 &'.format(CONFIG_DIR, 'gobgpd.conf'))


def run_bird(conf):
    c = CmdBuffer()
    c << 'router id {0};'.format(conf['target']['router-id'])
    c << 'listen bgp port 179;'
    c << 'protocol device { }'
    c << 'protocol direct {'
    c << '  disabled;'
    c << '}'
    c << 'protocol kernel {'
    c << '  disabled;'
    c << '}'
    c << 'table master;'
    for peer in conf['tester'].itervalues():
        c << 'table table_{0};'.format(peer['as'])
        c << 'protocol pipe pipe_{0} {{'.format(peer['as'])
        c << '  table master;'
        c << '  mode transparent;'
        c << '  peer table table_{0};'.format(peer['as'])
        c << '  import all;'
        c << '  export all;'
        c << '}'
        c << 'protocol bgp bgp_{0} {{'.format(peer['as'])
        c << '  local as {0};'.format(conf['target']['as'])
        n_addr = peer['local-address'].split('/')[0]
        c << '  neighbor {0} as {1};'.format(n_addr, peer['as'])
        c << '  import all;'
        c << '  export all;'
        c << '  rs client;'
        c << '}'

    with open('{0}/{1}'.format(CONFIG_DIR, 'bird.conf'), 'w') as f:
        f.write(str(c))

    if 'docker' in conf['target']:
        DOCKER_SHARED_DIR = '/etc/bird'
        name = 'bird'
        if conf['target']['docker'] and 'name' in conf['target']['docker']:
            name = conf['target']['docker']['name']

        image = 'osrg/bird'
        if conf['target']['docker'] and 'image' in conf['target']['docker']:
            image = conf['target']['docker']['image']

        c = CmdBuffer(' ')
        c << 'docker run --privileged=true'
        c << '-v {0}:{1}'.format(CONFIG_DIR, DOCKER_SHARED_DIR)
        c << '--name {0} -id {1}'.format(name, image)
        with settings(warn_only=True):
            local('docker rm -f {0}'.format(name))
        local(str(c))
        local('docker exec {0} ip li set up dev lo'.format(name))
        local('pipework {0} {1} {2}'.format(BR_NAME, name, conf['target']['local-address']))
        def _start_bird():
            c = CmdBuffer()
            c << '#!/bin/bash'
            c << 'bird'
            cmd = 'echo "{0:s}" > {1}/start.sh'.format(c, CONFIG_DIR)
            local(cmd)
            cmd = 'chmod 755 {0}/start.sh'.format(CONFIG_DIR)
            local(cmd)
            local('docker exec {0} {1}/start.sh'.format(name, DOCKER_SHARED_DIR))
        _start_bird()
    else:
        local('ip a add {0} dev {1}'.format(conf['target']['local-address'], BR_NAME))
        with settings(warn_only=True):
            local('pkill bird')
        local('bird -c {0}/{1}'.format(CONFIG_DIR, 'bird.conf'))


def run_target(conf):
    if conf['target']['type'] == 'gobgp':
        run_gobgp(conf)
    elif conf['target']['type'] == 'bird':
        run_bird(conf)
    else:
        raise Exception('unsupported target type')


def run_tester(conf):
    DOCKER_SHARED_DIR = '/root/shared_volume'
    name = 'tester'
    image = 'exabgp'
    c = CmdBuffer(' ')
    c << 'docker run --privileged=true'
    c << '-v {0}:{1}'.format(CONFIG_DIR, DOCKER_SHARED_DIR)
    c << '--name {0} -id {1} bash'.format(name, image)
    with settings(warn_only=True):
        local('docker rm -f {0}'.format(name))

    local(str(c))
    local('docker exec {0} ip li set up dev lo'.format(name))
    local('pipework {0} {1} 0/0'.format(BR_NAME, name))

    startup_script = CmdBuffer('\n')
    startup_script << "#!/bin/sh"
    for peer in conf['tester'].itervalues():
        startup_script << 'ip a add {0} dev eth1'.format(peer['local-address'])
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

        with open('{0}/exabgp/{1}.conf'.format(CONFIG_DIR, peer['router-id']), 'w') as f:
            f.write(str(cmd))

        cmd = CmdBuffer(' ')
        cmd << 'env exabgp.log.destination={0}/exabgp/{1}.log'.format(DOCKER_SHARED_DIR, peer['router-id'])
        cmd << 'exabgp.daemon.daemonize=true'
        cmd << 'exabgp.daemon.user=root'
        cmd << '/root/exabgp/sbin/exabgp {0}/exabgp/{1}.conf'.format(DOCKER_SHARED_DIR, peer['router-id'])
        startup_script << str(cmd)

    with open('{0}/exabgp/startup.sh'.format(CONFIG_DIR), 'w') as f:
        f.write(str(startup_script))

    local('chmod +x {0}/exabgp/startup.sh'.format(CONFIG_DIR))
    local('docker exec {0} {1}/exabgp/startup.sh'.format(name, DOCKER_SHARED_DIR))

def main():
    parser = ArgumentParser(description='BGP performance measuring tool')
    parser.add_argument('-f', '--file', default='bgperf.yml', type=str)
    args = parser.parse_args()

    with settings(warn_only=True):
        local('ip li del dev {0}'.format(BR_NAME))
        local('rm -r {0}'.format(CONFIG_DIR))

    local('ip li add {0} type bridge'.format(BR_NAME))
    local('ip li set up dev {0}'.format(BR_NAME))
    local('mkdir -p {0}/exabgp'.format(CONFIG_DIR))

    with open(args.file) as f:
        conf = yaml.load(f)
        run_target(conf)
        run_tester(conf)
