# Copyright (C) 2016 Nippon Telegraph and Telephone Corporation.
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

from settings import dckr
import io
import os
import yaml
from pyroute2 import IPRoute
from itertools import chain
from nsenter import Namespace
from threading import Thread
import netaddr

flatten = lambda l: chain.from_iterable(l)

def ctn_exists(name):
    return '/{0}'.format(name) in list(flatten(n['Names'] for n in dckr.containers(all=True)))


def img_exists(name):
    return name in [ctn['RepoTags'][0].split(':')[0] for ctn in dckr.images() if ctn['RepoTags'] != None]


class Container(object):
    def __init__(self, name, image, host_dir, guest_dir, conf):
        self.name = name
        self.image = image
        self.host_dir = host_dir
        self.guest_dir = guest_dir
        self.conf = conf
        self.config_name = None
        if not os.path.exists(host_dir):
            os.makedirs(host_dir)
            os.chmod(host_dir, 0777)

    def use_existing_config(self, name):
        if 'config_path' in self.conf:
            with open('{0}/{1}'.format(self.host_dir, name), 'w') as f:
                with open(self.conf['config_path'], 'r') as orig:
                    f.write(orig.read())
            self.config_name = name
            return True
        return False

    @classmethod
    def build_image(cls, force, tag, nocache=False):
        def insert_after_from(dockerfile, line):
            lines = dockerfile.split('\n')
            i = -1
            for idx, l in enumerate(lines):
                elems = [e.strip() for e in l.split()]
                if len(elems) > 0 and elems[0] == 'FROM':
                    i = idx
            if i < 0:
                raise Exception('no FROM statement')
            lines.insert(i+1, line)
            return '\n'.join(lines)

        for env in ['http_proxy', 'https_proxy']:
            if env in os.environ:
                cls.dockerfile = insert_after_from(cls.dockerfile, 'ENV {0} {1}'.format(env, os.environ[env]))

        f = io.BytesIO(cls.dockerfile.encode('utf-8'))
        if force or not img_exists(tag):
            print 'build {0}...'.format(tag)
            for line in dckr.build(fileobj=f, rm=True, tag=tag, decode=True, nocache=nocache):
                if 'stream' in line:
                    print line['stream'].strip()

    def get_ipv4_addresses(self):
        if 'local-address' in self.conf:
            local_addr = self.conf['local-address']
            if '/' in local_addr:
                local_addr = local_addr.split('/')[0]
            return [local_addr]
        raise NotImplementedError()

    def run(self, brname='', rm=True):

        if rm and ctn_exists(self.name):
            print 'remove container:', self.name
            dckr.remove_container(self.name, force=True)

        host_config = dckr.create_host_config(
            binds=['{0}:{1}'.format(os.path.abspath(self.host_dir), self.guest_dir)],
            privileged=True,
            network_mode='bridge',
            cap_add=['NET_ADMIN']
        )
        ctn = dckr.create_container(image=self.image, entrypoint='bash', detach=True, name=self.name,
                                    stdin_open=True, volumes=[self.guest_dir], host_config=host_config)
        self.ctn_id = ctn['Id']

        ipv4_addresses = self.get_ipv4_addresses()

        net_id = None
        for network in dckr.networks(names=[brname]):
            net_id = network['Id']
            if not 'IPAM' in network:
                print('can\'t verify if container\'s IP addresses '
                      'are valid for network {}: missing IPAM'.format(brname))
                break
            ipam = network['IPAM']

            if not 'Config' in ipam:
                print('can\'t verify if container\'s IP addresses '
                      'are valid for network {}: missing IPAM.Config'.format(brname))
                break

            ip_ok = False
            for ip in ipv4_addresses:
                for item in ipam['Config']:
                    if not 'Subnet' in item:
                        continue
                    subnet = item['Subnet']
                    ip_ok = netaddr.IPAddress(ip) in netaddr.IPNetwork(subnet)
                if not ip_ok:
                    raise Exception('the container\'s IP address {} is not valid for network {} '
                                    'since it\'s not part of any of its subnets'.format(
                                        ip, brname
                                    )
                                )
            break

        if net_id is None:
            print 'network "{}" not found!'.format(brname)
            return

        dckr.connect_container_to_network(self.ctn_id, net_id, ipv4_address=ipv4_addresses[0])
        dckr.start(container=self.name)

        if len(ipv4_addresses) > 1:

            # get the interface used by the first IP address already added by Docker
            dev = None
            exec_cmd = dckr.exec_create(self.ctn_id, 'ip addr', privileged=True)
            res = dckr.exec_start(exec_cmd['Id'])
            for line in res.split('\n'):
                if ipv4_addresses[0] in line:
                    dev = line.split(' ')[-1].strip()
            if not dev:
                dev = "eth0"

            for ip in ipv4_addresses[1:]:
                exec_cmd = dckr.exec_create(self.ctn_id, "ip addr add {} dev {}".format(ip, dev),
                                            privileged=True)
                dckr.exec_start(exec_cmd['Id'])

        return ctn

    def stats(self, queue):
        def stats():
            for stat in dckr.stats(self.ctn_id, decode=True):
                cpu_percentage = 0.0
                prev_cpu = stat['precpu_stats']['cpu_usage']['total_usage']
                if 'system_cpu_usage' in stat['precpu_stats']:
                    prev_system = stat['precpu_stats']['system_cpu_usage']
                else:
                    prev_system = 0
                cpu = stat['cpu_stats']['cpu_usage']['total_usage']
                system = stat['cpu_stats']['system_cpu_usage']
                cpu_num = len(stat['cpu_stats']['cpu_usage']['percpu_usage'])
                cpu_delta = float(cpu) - float(prev_cpu)
                system_delta = float(system) - float(prev_system)
                if system_delta > 0.0 and cpu_delta > 0.0:
                    cpu_percentage = (cpu_delta / system_delta) * float(cpu_num) * 100.0
                queue.put({'who': self.name, 'cpu': cpu_percentage, 'mem': stat['memory_stats']['usage']})

        t = Thread(target=stats)
        t.daemon = True
        t.start()
