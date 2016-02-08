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

class GoBGP(Container):
    def __init__(self, name, host_dir, guest_dir='/root/config', image='bgperf/gobgp'):
        super(GoBGP, self).__init__(name, image, host_dir, guest_dir)

    @classmethod
    def build_image(cls, force=False, tag='bgperf/gobgp'):
        cls.dockerfile = '''
FROM golang:1.6
WORKDIR /root
RUN go get -v github.com/osrg/gobgp/gobgpd
RUN go get -v github.com/osrg/gobgp/gobgp
RUN go install github.com/osrg/gobgp/gobgpd
RUN go install github.com/osrg/gobgp/gobgp
'''
        super(GoBGP, cls).build_image(force, tag)


    def write_config(self, conf, name='gobgpd.conf'):
        config = {}
        config['global'] = {
            'config': {
                'as': conf['target']['as'],
                'router-id': conf['target']['router-id']
            },
        }
        def gen_neighbor_config(n):
            return {'config': {'neighbor-address': n['local-address'].split('/')[0], 'peer-as': n['as']},
                    'transport': {'config': {'local-address': conf['target']['local-address'].split('/')[0]}},
                    'route-server': {'config': {'route-server-client': True}}}

        config['neighbors'] = [gen_neighbor_config(n) for n in conf['tester'].values() + [conf['monitor']]]
        with open('{0}/{1}'.format(self.host_dir, name), 'w') as f:
            f.write(yaml.dump(config))
        self.config_name = name

    def run(self, conf, brname=''):
        ctn = super(GoBGP, self).run(brname)

        if self.config_name == None:
            self.write_config(conf)

        startup = '''#!/bin/bash
ulimit -n 65536
ip a add {0} dev eth1
gobgpd -t yaml -f {1}/{2} -l {3} > {1}/gobgpd.log 2>&1
'''.format(conf['target']['local-address'], self.guest_dir, self.config_name, 'info')
        filename = '{0}/start.sh'.format(self.host_dir)
        with open(filename, 'w') as f:
            f.write(startup)
        os.chmod(filename, 0777)
        i = dckr.exec_create(container=self.name, cmd='{0}/start.sh'.format(self.guest_dir))
        dckr.exec_start(i['Id'], detach=True)

        return ctn
