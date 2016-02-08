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
import time
import shutil
from argparse import ArgumentParser, REMAINDER
from itertools import chain
from requests.exceptions import ConnectionError
from pyroute2 import IPRoute
from nsenter import Namespace
from base import *
from exabgp import ExaBGP
from gobgp import GoBGP
from bird import BIRD
from quagga import Quagga
from tester import Tester
from monitor import Monitor
from settings import dckr
from Queue import Queue

def rm_line():
    print '\x1b[1A\x1b[2K\x1b[1D\x1b[1A'


def gc_thresh3():
    gc_thresh3 = '/proc/sys/net/ipv4/neigh/default/gc_thresh3'
    with open(gc_thresh3) as f:
        return int(f.read().strip())


def doctor(args):
    ver = dckr.version()['Version']
    ok = int(''.join(ver.split('.'))) >= 190
    print 'docker version ... {1} ({0})'.format(ver, 'ok' if ok else 'update to 1.9.0 at least')

    print 'bgperf image',
    if img_exists('bgperf/exabgp'):
        print '... ok'
    else:
        print '... not found. run `bgperf prepare`'

    for name in ['gobgp', 'bird', 'quagga']:
        print '{0} image'.format(name),
        if img_exists('bgperf/{0}'.format(name)):
            print '... ok'
        else:
            print '... not found. if you want to bench {0}, run `bgperf prepare`'.format(name)

    print '/proc/sys/net/ipv4/neigh/default/gc_thresh3 ... {0}'.format(gc_thresh3())


def prepare(args):
    ExaBGP.build_image(args.force)
    GoBGP.build_image(args.force)
    Quagga.build_image(args.force)
    BIRD.build_image(args.force)


def update(args):
    ExaBGP.build_image(True)
    GoBGP.build_image(True)
    Quagga.build_image(True)
    BIRD.build_image(True)


def bench(args):
    config_dir = '{0}/{1}'.format(args.dir, args.bench_name)
    brname = args.bench_name + '-br'

    ip = IPRoute()
    ctn_intfs = flatten((l.get_attr('IFLA_IFNAME') for l in ip.get_links() if l.get_attr('IFLA_MASTER') == br) for br in ip.link_lookup(ifname=brname))

    if not args.repeat:
        # currently ctn name is same as ctn intf
        # TODO support proper mapping between ctn name and intf name
        for ctn in ctn_intfs:
            dckr.remove_container(ctn, force=True)

        if os.path.exists(config_dir):
            shutil.rmtree(config_dir)
    else:
        for ctn in ctn_intfs:
            if ctn != 'tester':
                dckr.remove_container(ctn, force=True)

    if args.file:
        with open(args.file) as f:
            conf = yaml.load(f)
    else:
        conf = gen_conf(args.neighbor_num, args.prefix_num)

    if len(conf['tester']) > gc_thresh3():
        print 'gc_thresh3({0}) is lower than the number of peer({1})'.format(gc_thresh3(), len(conf['tester']))
        print 'type next to increase the value'
        print '$ echo 16384 | sudo tee /proc/sys/net/ipv4/neigh/default/gc_thresh3'

    print 'run', args.target
    if args.target == 'gobgp':
        target = GoBGP
    elif args.target == 'bird':
        target = BIRD
    elif args.target == 'quagga':
        target = Quagga
    target = target(args.target, '{0}/{1}'.format(config_dir, args.target))
    target = target.run(conf, brname)

    print 'run monitor'
    m = Monitor('monitor', config_dir+'/monitor')
    m.run(conf, brname)

    print 'waiting bgp connection between {0} and monitor'.format(args.target)
    m.wait_established(conf['target']['local-address'].split('/')[0])

    t = Tester('tester', config_dir+'/tester')
    t.run(conf, brname)

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

    conf['monitor'] = {
        'as': 1001,
        'router-id': '10.10.0.2',
        'local-address': '10.10.0.2/16',
    }

    conf['tester'] = {}
    offset = 0
    for i in range(3, neighbor+3):
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
    s = parser.add_subparsers()
    parser_doctor = s.add_parser('doctor', help='check env')
    parser_doctor.set_defaults(func=doctor)

    parser_prepare = s.add_parser('prepare', help='prepare env')
    parser_prepare.add_argument('-f', '--force', default=False, type=bool)
    parser_prepare.set_defaults(func=prepare)

    parser_update = s.add_parser('update', help='pull bgp docker images')
    parser_update.add_argument('image', choices=['gobgp', 'bird', 'quagga', 'all'])
    parser_update.set_defaults(func=update)

    parser_bench = s.add_parser('bench', help='run benchmarks')
    parser_bench.add_argument('-t', '--target', choices=['gobgp', 'bird', 'quagga'], default='gobgp')
    parser_bench.add_argument('-i', '--image', help='specify custom docker image')
    parser_bench.add_argument('-r', '--repeat', action='store_true')
    parser_bench.add_argument('-f', '--file', metavar='CONFIG_FILE')
    parser_bench.add_argument('-n', '--neighbor-num', default=100, type=int)
    parser_bench.add_argument('-p', '--prefix-num', default=100, type=int)
    parser_bench.set_defaults(func=bench)

    parser_config = s.add_parser('config', help='generate config')
    parser_config.add_argument('-o', '--output', default='bgperf.yml', type=str)
    parser_config.add_argument('-n', '--neighbor-num', default=100, type=int)
    parser_config.add_argument('-p', '--prefix-num', default=100, type=int)
    parser_config.set_defaults(func=config)


    args = parser.parse_args()
    args.func(args)
