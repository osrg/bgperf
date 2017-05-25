# Copyright (C) 2017 Nippon Telegraph and Telephone Corporation.
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

from base import Tester
from gobgp import GoBGP
import os
import yaml
from  settings import dckr
import shutil

class GoBGPMRTTester(Tester, GoBGP):

    CONTAINER_NAME_PREFIX = 'bgperf_gobgp_mrttester_'

    def __init__(self, name, host_dir, conf, image='bgperf/gobgp'):
        super(GoBGPMRTTester, self).__init__(name, host_dir, conf, image)

    def configure_neighbors(self, target_conf):
        conf = self.conf.get('neighbors', {}).values()[0]

        config = {
            'global': {
                'config': {
                    'as': conf['as'],
                    'router-id': conf['router-id'],
                }
            },
            'neighbors': [
                {
                    'config': {
                        'neighbor-address': target_conf['local-address'],
                        'peer-as': target_conf['as']
                    }
                }
            ]
        }

        with open('{0}/{1}.conf'.format(self.host_dir, self.name), 'w') as f:
            f.write(yaml.dump(config, default_flow_style=False))
            self.config_name = '{0}.conf'.format(self.name)

    def get_startup_cmd(self):
        conf = self.conf.get('neighbors', {}).values()[0]

        mrtfile = '{0}/{1}'.format(self.host_dir, os.path.basename(conf['mrt-file']))
        shutil.copyfile(conf['mrt-file'], mrtfile)

        startup = '''#!/bin/bash
ulimit -n 65536
gobgpd -t yaml -f {1}/{2} -l {3} > {1}/gobgpd.log 2>&1 &
'''.format(conf['local-address'], self.guest_dir, self.config_name, 'info')

        cmd = ['gobgp', 'mrt']
        if conf.get('only-best', False):
            cmd.append('--only-best')
        cmd += ['inject', 'global', '{0}/{1}'.format(self.guest_dir, os.path.basename(conf['mrt-file']))]
        if 'count' in conf:
            cmd.append(str(conf['count']))
        if 'skip' in conf:
            cmd.append(str(conf['skip']))

        startup += '\n' + ' '.join(cmd)

        startup += '\n' + 'pkill -SIGHUP gobgpd'
        return startup
