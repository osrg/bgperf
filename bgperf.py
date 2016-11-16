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
import netaddr
import datetime
from argparse import ArgumentParser, REMAINDER
from itertools import chain, islice
from requests.exceptions import ConnectionError
from pyroute2 import IPRoute
from socket import AF_INET
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
    ExaBGP.build_image(args.force, nocache=args.no_cache)
    GoBGP.build_image(args.force, nocache=args.no_cache)
    Quagga.build_image(args.force, nocache=args.no_cache)
    BIRD.build_image(args.force, nocache=args.no_cache)


def update(args):
    if args.image == 'all' or args.image == 'exabgp':
        ExaBGP.build_image(True, checkout=args.checkout, nocache=args.no_cache)
    if args.image == 'all' or args.image == 'gobgp':
        GoBGP.build_image(True, checkout=args.checkout, nocache=args.no_cache)
    if args.image == 'all' or args.image == 'quagga':
        Quagga.build_image(True, checkout=args.checkout, nocache=args.no_cache)
    if args.image == 'all' or args.image == 'bird':
        BIRD.build_image(True, checkout=args.checkout, nocache=args.no_cache)


def bench(args):
    config_dir = '{0}/{1}'.format(args.dir, args.bench_name)
    brname = args.bench_name + '-br'

    ip = IPRoute()
    ctn_intfs = flatten((l.get_attr('IFLA_IFNAME') for l in ip.get_links() if l.get_attr('IFLA_MASTER') == br) for br in ip.link_lookup(ifname=brname))

    if not args.repeat:
        # currently ctn name is same as ctn intf
        # TODO support proper mapping between ctn name and intf name
        for ctn in ctn_intfs:
            dckr.remove_container(ctn, force=True) if ctn_exists(ctn) else None

        if os.path.exists(config_dir):
            shutil.rmtree(config_dir)
    else:
        for ctn in ctn_intfs:
            if ctn != 'tester':
                dckr.remove_container(ctn, force=True) if ctn_exists(ctn) else None

    if args.file:
        with open(args.file) as f:
            conf = yaml.load(f)
    else:
        conf = gen_conf(args)
        if not os.path.exists(config_dir):
            os.makedirs(config_dir)
        with open('{0}/scenario.yaml'.format(config_dir), 'w') as f:
            f.write(yaml.dump(conf))

    if len(conf['tester']) > gc_thresh3():
        print 'gc_thresh3({0}) is lower than the number of peer({1})'.format(gc_thresh3(), len(conf['tester']))
        print 'type next to increase the value'
        print '$ echo 16384 | sudo tee /proc/sys/net/ipv4/neigh/default/gc_thresh3'

    if args.target == 'gobgp':
        target = GoBGP
    elif args.target == 'bird':
        target = BIRD
    elif args.target == 'quagga':
        target = Quagga

    is_remote = True if 'remote' in conf['target'] and conf['target']['remote'] else False

    if is_remote:
        r = ip.get_routes(dst=conf['target']['local-address'].split('/')[0], family=AF_INET)
        if len(r) == 0:
            print 'no route to remote target {0}'.format(conf['target']['local-address'])
            sys.exit(1)

        idx = [t[1] for t in r[0]['attrs'] if t[0] == 'RTA_OIF'][0]
        intf = ip.get_links(idx)[0]

        if intf.get_attr('IFLA_MASTER') not in ip.link_lookup(ifname=brname):
            br = ip.link_lookup(ifname=brname)
            if len(br) == 0:
                ip.link_create(ifname=brname, kind='bridge')
                br = ip.link_lookup(ifname=brname)
            br = br[0]
            ip.link('set', index=idx, master=br)
    else:
        print 'run', args.target
        if args.image:
            target = target(args.target, '{0}/{1}'.format(config_dir, args.target), image=args.image)
        else:
            target = target(args.target, '{0}/{1}'.format(config_dir, args.target))
        target.run(conf, brname)

    print 'run monitor'
    m = Monitor('monitor', config_dir+'/monitor')
    m.run(conf, brname)

    time.sleep(1)

    print 'waiting bgp connection between {0} and monitor'.format(args.target)
    m.wait_established(conf['target']['local-address'].split('/')[0])

    if not args.repeat:
        print 'run tester'
        t = Tester('tester', config_dir+'/tester')
        t.run(conf, brname)

    start = datetime.datetime.now()

    q = Queue()

    m.stats(q)
    if not is_remote:
        target.stats(q)

    def mem_human(v):
        if v > 1000 * 1000 * 1000:
            return '{0:.2f}GB'.format(float(v) / (1000 * 1000 * 1000))
        elif v > 1000 * 1000:
            return '{0:.2f}MB'.format(float(v) / (1000 * 1000))
        elif v > 1000:
            return '{0:.2f}KB'.format(float(v) / 1000)
        else:
            return '{0:.2f}B'.format(float(v))

    f = open(args.output, 'w') if args.output else None
    cpu = 0
    mem = 0
    cooling = -1
    while True:
        info = q.get()

        if not is_remote and info['who'] == target.name:
            cpu = info['cpu']
            mem = info['mem']

        if info['who'] == m.name:
            now = datetime.datetime.now()
            elapsed = now - start
            recved = info['state']['adj-table']['accepted'] if 'accepted' in info['state']['adj-table'] else 0
            if elapsed.seconds > 0:
                rm_line()
            print 'elapsed: {0}sec, cpu: {1:>4.2f}%, mem: {2}, recved: {3}'.format(elapsed.seconds, cpu, mem_human(mem), recved)
            f.write('{0}, {1}, {2}, {3}\n'.format(elapsed.seconds, cpu, mem, recved)) if f else None
            f.flush() if f else None

            if cooling == args.cooling:
                f.close() if f else None
                return

            if cooling >= 0:
                cooling += 1

            if info['checked']:
                cooling = 0

def gen_conf(args):
    neighbor = args.neighbor_num
    prefix = args.prefix_num
    as_path_list = args.as_path_list_num
    prefix_list = args.prefix_list_num
    community_list = args.community_list_num
    ext_community_list = args.ext_community_list_num

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
        'check-points': [prefix * neighbor],
    }

    conf['tester'] = {}
    offset = 0

    it = netaddr.iter_iprange('90.0.0.0', '100.0.0.0')

    conf['policy'] = {}

    assignment = []

    if prefix_list > 0:
        name = 'p1'
        conf['policy'][name] = {
            'match': [{
                'type': 'prefix',
                'value': list('{0}/32'.format(ip) for ip in islice(it, prefix_list)),
            }],
        }
        assignment.append(name)

    if as_path_list > 0:
        name = 'p2'
        conf['policy'][name] = {
            'match': [{
                'type': 'as-path',
                'value': list(range(10000, 10000 + as_path_list)),
            }],
        }
        assignment.append(name)

    if community_list > 0:
        name = 'p3'
        conf['policy'][name] = {
            'match': [{
                'type': 'community',
                'value': list('{0}:{1}'.format(i/(1<<16), i%(1<<16)) for i in range(community_list)),
            }],
        }
        assignment.append(name)

    if ext_community_list > 0:
        name = 'p4'
        conf['policy'][name] = {
            'match': [{
                'type': 'ext-community',
                'value': list('rt:{0}:{1}'.format(i/(1<<16), i%(1<<16)) for i in range(ext_community_list)),
            }],
        }
        assignment.append(name)

    it = netaddr.iter_iprange('100.0.0.0','160.0.0.0')
    for i in range(3, neighbor+3):
        router_id = '10.10.{0}.{1}'.format(i/255, i%255)
        conf['tester'][router_id] = {
            'as': 1000 + i,
            'router-id': router_id,
            'local-address': router_id + '/16',
            'paths': list('{0}/32'.format(ip) for ip in islice(it, prefix)),
            'filter': {
                args.filter_type: assignment,
            },
        }
    return conf


def config(args):
    conf = gen_conf(args)

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
    parser_prepare.add_argument('-f', '--force', action='store_true', help='build even if the container already exists')
    parser_prepare.add_argument('-n', '--no-cache', action='store_true')
    parser_prepare.set_defaults(func=prepare)

    parser_update = s.add_parser('update', help='rebuild bgp docker images')
    parser_update.add_argument('image', choices=['exabgp', 'gobgp', 'bird', 'quagga', 'all'])
    parser_update.add_argument('-c', '--checkout', default='HEAD')
    parser_update.add_argument('-n', '--no-cache', action='store_true')
    parser_update.set_defaults(func=update)

    parser_bench = s.add_parser('bench', help='run benchmarks')
    parser_bench.add_argument('-t', '--target', choices=['gobgp', 'bird', 'quagga'], default='gobgp')
    parser_bench.add_argument('-i', '--image', help='specify custom docker image')
    parser_bench.add_argument('-r', '--repeat', action='store_true', help='use existing tester/monitor container')
    parser_bench.add_argument('-f', '--file', metavar='CONFIG_FILE')
    parser_bench.add_argument('-n', '--neighbor-num', default=100, type=int)
    parser_bench.add_argument('-p', '--prefix-num', default=100, type=int)
    parser_bench.add_argument('-l', '--filter-type', choices=['in', 'out'], default='in')
    parser_bench.add_argument('-a', '--as-path-list-num', default=0, type=int)
    parser_bench.add_argument('-e', '--prefix-list-num', default=0, type=int)
    parser_bench.add_argument('-c', '--community-list-num', default=0, type=int)
    parser_bench.add_argument('-x', '--ext-community-list-num', default=0, type=int)
    parser_bench.add_argument('-g', '--cooling', default=0, type=int)
    parser_bench.add_argument('-o', '--output', metavar='STAT_FILE')
    parser_bench.set_defaults(func=bench)

    parser_config = s.add_parser('config', help='generate config')
    parser_config.add_argument('-o', '--output', default='bgperf.yml', type=str)
    parser_config.add_argument('-n', '--neighbor-num', default=100, type=int)
    parser_config.add_argument('-p', '--prefix-num', default=100, type=int)
    parser_config.add_argument('-l', '--filter-type', choices=['in', 'out'], default='in')
    parser_config.add_argument('-a', '--as-path-list-num', default=0, type=int)
    parser_config.add_argument('-e', '--prefix-list-num', default=0, type=int)
    parser_config.add_argument('-c', '--community-list-num', default=0, type=int)
    parser_config.add_argument('-x', '--ext-community-list-num', default=0, type=int)
    parser_config.set_defaults(func=config)


    args = parser.parse_args()
    args.func(args)
