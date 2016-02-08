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

from base import *

class Quagga(Container):
    def __init__(self, name, host_dir, guest_dir='/root/config', image='bgperf/quagga'):
        super(Quagga, self).__init__(name, image, host_dir, guest_dir)

    @classmethod
    def build_image(cls, force=False, tag='bgperf/quagga'):
        cls.dockerfile = '''
FROM ubuntu:latest
WORKDIR /root
RUN useradd -M quagga
RUN mkdir /var/log/quagga && chown quagga:quagga /var/log/quagga
RUN mkdir /var/run/quagga && chown quagga:quagga /var/run/quagga
RUN apt-get update && apt-get install -qy git autoconf libtool gawk make telnet libreadline6-dev
RUN git clone git://git.sv.gnu.org/quagga.git quagga && \
(cd quagga && ./bootstrap.sh && \
./configure --disable-doc --localstatedir=/var/run/quagga && make && make install)
RUN ldconfig
'''
        super(Quagga, cls).build_image(force, tag)

    def write_config(self, conf, name='bgpd.conf'):
        config = """hostname bgpd
password zebra
router bgp {0}
bgp router-id {1}
""".format(conf['target']['as'], conf['target']['router-id'])

        def gen_neighbor_config(n):
            return """neighbor {0} remote-as {1}
neighbor {0} route-server-client
""".format(n['local-address'].split('/')[0], n['as'])

        with open('{0}/{1}'.format(self.host_dir, name), 'w') as f:
            f.write(config)
            for n in conf['tester'].values() + [conf['monitor']]:
                f.write(gen_neighbor_config(n))
        self.config_name = name

    def run(self, conf, brname=''):
        ctn = super(Quagga, self).run(brname)

        if self.config_name == None:
            self.write_config(conf)

        startup = '''#!/bin/bash
ulimit -n 65536
ip a add {0} dev eth1
bgpd -u root -f {1}/{2}
'''.format(conf['target']['local-address'], self.guest_dir, self.config_name)
        filename = '{0}/start.sh'.format(self.host_dir)
        with open(filename, 'w') as f:
            f.write(startup)
        os.chmod(filename, 0777)
        i = dckr.exec_create(container=self.name, cmd='{0}/start.sh'.format(self.guest_dir))
        dckr.exec_inspect(i['Id'])
        dckr.exec_start(i['Id'], detach=True)
        return ctn
