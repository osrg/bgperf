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

flatten = lambda l: chain.from_iterable(l)

def ctn_exists(name):
    return '/{0}'.format(name) in list(flatten(n['Names'] for n in dckr.containers(all=True)))


def img_exists(name):
    return name in [ctn['RepoTags'][0].split(':')[0] for ctn in dckr.images() if ctn['RepoTags'] != None]


class docker_netns(object):
    def __init__(self, name):
        pid = int(dckr.inspect_container(name)['State']['Pid'])
        if pid == 0:
            raise Exception('no container named {0}'.format(name))
        self.pid = pid

    def __enter__(self):
        pid = self.pid
        if not os.path.exists('/var/run/netns'):
            os.mkdir('/var/run/netns')
        os.symlink('/proc/{0}/ns/net'.format(pid), '/var/run/netns/{0}'.format(pid))
        return str(pid)

    def __exit__(self, type, value, traceback):
        pid = self.pid
        os.unlink('/var/run/netns/{0}'.format(pid))


def connect_ctn_to_br(ctn, brname):
    with docker_netns(ctn) as pid:
        ip = IPRoute()
        br = ip.link_lookup(ifname=brname)
        if len(br) == 0:
            ip.link_create(ifname=brname, kind='bridge')
            br = ip.link_lookup(ifname=brname)
        br = br[0]
        ip.link('set', index=br, state='up')

        ifs = ip.link_lookup(ifname=ctn)
        if len(ifs) > 0:
           ip.link_remove(ifs[0])

        ip.link_create(ifname=ctn, kind='veth', peer=pid)
        host = ip.link_lookup(ifname=ctn)[0]
        ip.link('set', index=host, master=br)
        ip.link('set', index=host, state='up')
        guest = ip.link_lookup(ifname=pid)[0]
        ip.link('set', index=guest, net_ns_fd=pid)
        with Namespace(pid, 'net'):
            ip = IPRoute()
            ip.link('set', index=guest, ifname='eth1')
            ip.link('set', index=guest, state='up')


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

    def run(self, brname='', rm=True):

        if rm and ctn_exists(self.name):
            print 'remove container:', self.name
            dckr.remove_container(self.name, force=True)

        config = dckr.create_host_config(binds=['{0}:{1}'.format(os.path.abspath(self.host_dir), self.guest_dir)],
                                         privileged=True)
        ctn = dckr.create_container(image=self.image, entrypoint='bash', detach=True, name=self.name,
                                    stdin_open=True, volumes=[self.guest_dir], host_config=config)
        dckr.start(container=self.name)
        if brname != '':
            connect_ctn_to_br(self.name, brname)
        self.ctn_id = ctn['Id']

        return ctn

    def stats(self, queue):
        def stats():
            for stat in dckr.stats(self.ctn_id, decode=True):
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
                queue.put({'who': self.name, 'cpu': cpu_percentage, 'mem': stat['memory_stats']['usage']})

        t = Thread(target=stats)
        t.daemon = True
        t.start()
