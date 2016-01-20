#!/usr/bin/env python
#
# Copyright (C) 2015, 2016 Nippon Telegraph and Telephone Corporation.
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

import os
import sys
import yaml
import shutil
import json
import io
from argparse import ArgumentParser, REMAINDER
from itertools import chain
from docker import Client
from requests.exceptions import ConnectionError


class CmdBuffer(list):
    def __init__(self, delim='\n'):
        super(CmdBuffer, self).__init__()
        self.delim = delim

    def __lshift__(self, value):
        self.append(value)

    def __str__(self):
        return self.delim.join(self)


def ctn_exists(name):
    return '/{0}'.format(name) in list(chain.from_iterable(n['Names'] for n in dckr.containers(all=True)))


def img_exists(name):
    return name in [ctn['RepoTags'][0].split(':')[0] for ctn in dckr.images()]


def connect_ctn_to_nw(ctn_name, nw_name):
    net_id = [n['Id'] for n in dckr.networks() if n['Name'] == nw_name][0]
    return dckr.connect_container_to_network(container=ctn_name, net_id=net_id)


def rm_line():
    print '\x1b[1A\x1b[2K\x1b[1D\x1b[1A'


def gc_thresh3():
    gc_thresh3 = '/proc/sys/net/ipv4/neigh/default/gc_thresh3'
    with open(gc_thresh3) as f:
        return int(f.read().strip())

def run_gobgp(args, conf):
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

    config_dir = '{0}/{1}'.format(args.dir, args.bench_name)
    with open('{0}/{1}'.format(config_dir, 'gobgpd.conf'), 'w') as f:
        f.write(yaml.dump(config))

    name = 'gobgp'
    if ctn_exists(name):
        print 'remove container:', name
        dckr.remove_container(name, force=True)

    docker_dir = '/root/config'
    host_config = dckr.create_host_config(binds=['{0}:{1}'.format(config_dir, docker_dir)],
                                          privileged=True)
    image = 'osrg/gobgp'
    if args.image:
        image = args.image
    ctn = dckr.create_container(image=image, detach=True, name=name, stdin_open=True,
                                volumes=[docker_dir], host_config=host_config)

    dckr.start(container=name)
    net_id = [n['Id'] for n in dckr.networks() if n['Name'] == args.bench_name][0]
    dckr.connect_container_to_network(container=name, net_id=net_id)

    c = CmdBuffer()
    c << '#!/bin/bash'
    c << "ulimit -n 65536"
    c << 'ip a add {0} dev eth1'.format(conf['target']['local-address'])
    c << '/go/bin/gobgpd -t yaml -f {0}/gobgpd.conf -l {1} > ' \
         '{0}/gobgpd.log 2>&1'.format(docker_dir, 'info')
    with open('{0}/start.sh'.format(config_dir), 'w') as f:
        f.write(str(c))
    os.chmod('{0}/start.sh'.format(config_dir), 0777)
    i = dckr.exec_create(container=name, cmd='{0}/start.sh'.format(docker_dir))
    dckr.exec_inspect(i['Id'])
    dckr.exec_start(i['Id'], detach=True)
    return ctn


def run_bird(args, conf):
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

    config_dir = '{0}/{1}'.format(args.dir, args.bench_name)
    with open('{0}/{1}'.format(config_dir, 'bird.conf'), 'w') as f:
        f.write(str(c))

    name = 'bird'
    if ctn_exists(name):
        print 'remove container:', name
        dckr.remove_container(name, force=True)

    docker_dir = '/etc/bird'
    host_config = dckr.create_host_config(binds=['{0}:{1}'.format(config_dir, docker_dir)],
                                          privileged=True)
    image = 'osrg/bird'
    if args.image:
        image = args.image
    ctn = dckr.create_container(image=image, detach=True, name=name, stdin_open=True,
                                volumes=[docker_dir], host_config=host_config)

    dckr.start(container=name)
    net_id = [n['Id'] for n in dckr.networks() if n['Name'] == args.bench_name][0]
    dckr.connect_container_to_network(container=name, net_id=net_id)

    c = CmdBuffer()
    c << '#!/bin/bash'
    c << "ulimit -n 65536"
    c << 'ip a add {0} dev eth1'.format(conf['target']['local-address'])
    c << 'bird'
    with open('{0}/start.sh'.format(config_dir), 'w') as f:
        f.write(str(c))
    os.chmod('{0}/start.sh'.format(config_dir), 0777)
    i = dckr.exec_create(container=name, cmd='{0}/start.sh'.format(docker_dir))
    dckr.exec_inspect(i['Id'])
    dckr.exec_start(i['Id'], detach=True)
    return ctn


def run_quagga(args, conf):
    c = CmdBuffer()
    c << 'hostname bgpd'
    c << 'password zebra'
    c << 'router bgp {0}'.format(conf['target']['as'])
    c << 'bgp router-id {0}'.format(conf['target']['router-id'])
    for peer in conf['tester'].itervalues():
        c << 'neighbor {0} remote-as {1}'.format(peer['local-address'].split('/')[0], peer['as'])
        c << 'neighbor {0} route-server-client'.format(peer['local-address'].split('/')[0])

    config_dir = '{0}/{1}'.format(args.dir, args.bench_name)
    with open('{0}/{1}'.format(config_dir, 'bgpd.conf'), 'w') as f:
        f.write(str(c))

    name = 'quagga'
    if ctn_exists(name):
        print 'remove container:', name
        dckr.remove_container(name, force=True)

    docker_dir = '/etc/quagga'
    host_config = dckr.create_host_config(binds=['{0}:{1}'.format(config_dir, docker_dir)],
                                          privileged=True)
    image = 'osrg/quagga'
    if args.image:
        image = args.image
    ctn = dckr.create_container(image=image, detach=True, name=name, stdin_open=True,
                                volumes=[docker_dir], host_config=host_config)
    dckr.start(container=name)
    net_id = [n['Id'] for n in dckr.networks() if n['Name'] == args.bench_name][0]
    dckr.connect_container_to_network(container=name, net_id=net_id)

    c = CmdBuffer()
    c << '#!/bin/bash'
    c << "ulimit -n 65536"
    c << 'ip a add {0} dev eth1'.format(conf['target']['local-address'])
    with open('{0}/start.sh'.format(config_dir), 'w') as f:
        f.write(str(c))
    os.chmod('{0}/start.sh'.format(config_dir), 0777)
    i = dckr.exec_create(container=name, cmd='{0}/start.sh'.format(docker_dir))
    dckr.exec_inspect(i['Id'])
    dckr.exec_start(i['Id'], detach=True)

    return ctn


def run_tester(args, conf):
    config_dir = '{0}/{1}'.format(args.dir, args.bench_name)
    docker_dir = '/root/config'
    host_config = dckr.create_host_config(binds=['{0}:{1}'.format(config_dir, docker_dir)],
                                          privileged=True)
    image = 'bgperf'
    name = args.bench_name
    ctn = dckr.create_container(image=image, command='bash', detach=True, name=name,
                                stdin_open=True, volumes=[docker_dir], host_config=host_config)
    dckr.start(container=name)
    connect_ctn_to_nw(name, args.bench_name)

    startup_script = CmdBuffer('\n')
    startup_script << "#!/bin/sh"
    startup_script << "ulimit -n 65536"
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

        with open('{0}/exabgp/{1}.conf'.format(config_dir, peer['router-id']), 'w') as f:
            f.write(str(cmd))

        cmd = CmdBuffer(' ')
        cmd << 'env exabgp.log.destination={0}/exabgp/{1}.log'.format(docker_dir, peer['router-id'])
        cmd << 'exabgp.daemon.daemonize=true'
        cmd << 'exabgp.daemon.user=root'
        cmd << '/root/exabgp/sbin/exabgp {0}/exabgp/{1}.conf'.format(docker_dir, peer['router-id'])
        startup_script << str(cmd)

    with open('{0}/exabgp/startup.sh'.format(config_dir), 'w') as f:
        f.write(str(startup_script))

    os.chmod('{0}/exabgp/startup.sh'.format(config_dir), 0777)
    i = dckr.exec_create(container=name, cmd='{0}/exabgp/startup.sh'.format(docker_dir))
    cnt = 0
    for lines in dckr.exec_start(i['Id'], stream=True):
        for line in lines.strip().split('\n'):
            cnt += 1
            if cnt % 2 == 1:
                if cnt > 1:
                    rm_line()
                print 'tester booting.. ({0}/{1})'.format(cnt/2 + 1, len(conf['tester']))

    return ctn


def doctor(args):
    try:
        dckr = Client()
        ver = dckr.version()['Version']
    except ConnectionError:
        print "can't connect to docker daemon"
        sys.exit(1)
    ok = int(''.join(ver.split('.'))) >= 190
    print 'docker version ... {1} ({0})'.format(ver, 'ok' if ok else 'update to 1.9.0 at least')

    print 'bgperf image',
    if img_exists('bgperf'):
        print '... ok'
    else:
        print '... not found. run `bgperf prepare`'

    for name in ['gobgp', 'bird', 'quagga']:
        print '{0} image'.format(name),
        if img_exists('osrg/{0}'.format(name)):
            print '... ok'
        else:
            print '... not found. if you want to bench {0}, run `bgperf prepare`'.format(name)

    print '/proc/sys/net/ipv4/neigh/default/gc_thresh3 ... {0}'.format(gc_thresh3())


def prepare(args):
    dockerfile = '''
FROM ubuntu:latest
WORKDIR /root
RUN apt-get install -qy git python
RUN git clone https://github.com/Exa-Networks/exabgp
RUN ln -s /root/exabgp /exabgp
'''
    f = io.BytesIO(dockerfile.encode('utf-8'))
    if not img_exists('bgperf'):
        print 'build tester container'
        for line in dckr.build(fileobj=f, rm=True, tag='bgperf', decode=True):
            print line['stream'].strip()

    images = ['gobgp', 'bird', 'quagga']
    for image in ['osrg/{0}'.format(n) for n in images]:
        if not img_exists(image):
            print 'pulling', image
            for line in dckr.pull(image, stream=True):
                print json.loads(line)['status'].strip()


def update(args):
    if args.image == 'all':
        images = ['gobgp', 'bird', 'quagga']
    else:
        images = [args.image]

    for image in ['osrg/{0}'.format(n) for n in images]:

        print 'pulling', image
        for line in dckr.pull(image, stream=True):
            print json.loads(line)['status'].strip()


def bench(args):
    config_dir = '{0}/{1}'.format(args.dir, args.bench_name)

    get_nw = lambda : [n for n in dckr.networks() if n['Name'] == args.bench_name]
    nws = get_nw()
    if not args.repeat:
        if len(nws) > 0:
            nw = nws[0]
            for ctn in nw['Containers'].keys():
                dckr.remove_container(ctn, force=True)
            dckr.remove_network(nw['Id'])

        if os.path.exists(config_dir):
            shutil.rmtree(config_dir)
    else:
        if len(nws) > 0:
            nw = nws[0]
            for k in nw['Containers'].keys():
                name = [c['Names'] for c in dckr.containers() if c['Id'] == k][0][0][1:]
                if name != 'bgperf':
                    print 'remove container:', name
                    dckr.remove_container(k, force=True)

    if len(get_nw()) == 0:
        dckr.create_network(name=args.bench_name)

    if not os.path.exists(config_dir):
        os.makedirs('{0}/exabgp'.format(config_dir))
        os.chmod('{0}/exabgp'.format(config_dir), 0777)

    if args.file:
        with open(args.file) as f:
            conf = yaml.load(f)
    else:
        conf = gen_conf(args.neighbor_num, args.prefix_num)

    if ctn_exists(args.bench_name) and not args.repeat:
        dckr.remove_container(args.bench_name, force=True)

    if len(conf['tester']) > gc_thresh3():
        print 'gc_thresh3({0}) is lower than the number of peer({1})'.format(gc_thresh3(), len(conf['tester']))
        print 'type next to increase the value'
        print '$ echo 16384 | sudo tee /proc/sys/net/ipv4/neigh/default/gc_thresh3'

    if not ctn_exists(args.bench_name):
        print 'run tester'
        run_tester(args, conf)

    print 'run', args.target
    if args.target == 'gobgp':
        target = run_gobgp(args, conf)
    elif args.target == 'bird':
        target = run_bird(args, conf)
    elif args.target == 'quagga':
        target = run_quagga(args, conf)

    idle_hold = 0
    idle_limit = 5
    if args.repeat:
        idle_limit = 20
    elapsed = 0
    calm_limit = 1.0 + float(4*len(conf['tester']))/1000

    def mem(v):
        if v > 1000 * 1000 * 1000:
            return '{0:.2f}GB'.format(float(v) / (1000 * 1000 * 1000))
        elif v > 1000 * 1000:
            return '{0:.2f}MB'.format(float(v) / (1000 * 1000))
        elif v > 1000:
            return '{0:.2f}KB'.format(float(v) / 1000)
        else:
            return '{0:.2f}B'.format(float(v))

    print 'calm_limit: {0}%, idle_limit: {1}s'.format(calm_limit, idle_limit)

    for stat in dckr.stats(target['Id'], decode=True):
        cpu_percentage = 0.0
        prev_cpu = stat['precpu_stats']['cpu_usage']['total_usage']
        prev_system = stat['precpu_stats']['system_cpu_usage']
        cpu = stat['cpu_stats']['cpu_usage']['total_usage']
        system = stat['cpu_stats']['system_cpu_usage']
        cpu_num = len(stat['cpu_stats']['cpu_usage']['percpu_usage'])
        cpu_delta = float(cpu) - float(prev_cpu)
        system_delta = float(system) - float(prev_system)
        if system_delta > 0.0 and cpu_delta > 0.0:
            cpu_percentage = (cpu_delta / system_delta) * float(cpu_num) * 100.0
        if elapsed > 0:
            rm_line()
        print 'elapsed: {0:>2}sec, cpu: {1:>4.2f}%, mem: {2}'.format(elapsed, cpu_percentage, mem(stat['memory_stats']['usage']))
        if cpu_percentage < calm_limit:
            idle_hold += 1
            if idle_hold == idle_limit:
                print 'elapsed time: {0}sec'.format(elapsed - idle_limit)
                break
        else:
            idle_hold = 0
        elapsed += 1


def gen_conf(neighbor, prefix):
    conf = {}
    conf['target'] = {
        'as': 1000,
        'router-id': '10.10.0.1',
        'local-address': '10.10.0.1/16',
    }
    conf['tester'] = {}
    offset = 0
    for i in range(2, neighbor+2):
        router_id = '10.10.{0}.{1}'.format(i/255, i%255)
        paths = []
        if (i+offset) % 254 + 1 == 224:
            offset += 16
        for j in range(prefix):
            paths.append('{0}.{1}.{2}.{3}/32'.format((i+offset)%254 + 1, (i+offset)/254 + 1, j/255, j%255))

        conf['tester'][router_id] = {
            'as': 1000 + i,
            'router-id': router_id,
            'local-address': router_id + '/16',
            'paths': paths,
        }
    return conf


def config(args):
    conf = gen_conf(args.neighbor_num, args.prefix_num)

    with open(args.output, 'w') as f:
        f.write(yaml.dump(conf))


if __name__ == '__main__':
    parser = ArgumentParser(description='BGP performance measuring tool')
    parser.add_argument('-b', '--bench-name', default='bgperf')
    parser.add_argument('-d', '--dir', default='/tmp')
    parser.add_argument('-n', '--neighbor-num', default=100, type=int)
    parser.add_argument('-p', '--prefix-num', default=100, type=int)
    s = parser.add_subparsers()
    parser_doctor = s.add_parser('doctor', help='check env')
    parser_doctor.set_defaults(func=doctor)

    parser_prepare = s.add_parser('prepare', help='prepare env')
    parser_prepare.set_defaults(func=prepare)

    parser_update = s.add_parser('update', help='pull bgp docker images')
    parser_update.add_argument('image', choices=['gobgp', 'bird', 'quagga', 'all'])
    parser_update.set_defaults(func=update)

    parser_bench = s.add_parser('bench', help='run benchmarks')
    parser_bench.add_argument('-t', '--target', choices=['gobgp', 'bird', 'quagga'], default='gobgp')
    parser_bench.add_argument('-i', '--image', help='specify custom docker image')
    parser_bench.add_argument('-r', '--repeat', action='store_true')
    parser_bench.add_argument('-f', '--file', metavar='CONFIG_FILE')
    parser_bench.set_defaults(func=bench)

    parser_config = s.add_parser('config', help='generate config')
    parser_config.add_argument('-o', '--output', default='bgperf.yml', type=str)
    parser_config.set_defaults(func=config)

    dckr = Client()

    args = parser.parse_args()
    args.func(args)
