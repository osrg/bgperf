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

from gobgp import GoBGP
import os
import yaml
from  settings import dckr
import shutil

class MRTTester(GoBGP):

    def run(self, conf, target, brname=''):
        ctn = super(GoBGP, self).run(brname)

        conf = conf['tester'].values()[0]

        config = {
            'global': {
                'config': {
                    'as': conf['as'],
                    'router-id': conf['router-id'],
                }
            },
        }

        with open('{0}/{1}.conf'.format(self.host_dir, self.name), 'w') as f:
            f.write(yaml.dump(config, default_flow_style=False))
            self.config_name = '{0}.conf'.format(self.name)

        startup = '''#!/bin/bash
ulimit -n 65536
ip a add {0} dev eth1
gobgpd -t yaml -f {1}/{2} -l {3} > {1}/gobgpd.log 2>&1
'''.format(conf['local-address'], self.guest_dir, self.config_name, 'info')
        filename = '{0}/start.sh'.format(self.host_dir)

        with open(filename, 'w') as f:
            f.write(startup)
        os.chmod(filename, 0777)
        i = dckr.exec_create(container=self.name, cmd='{0}/start.sh'.format(self.guest_dir))
        dckr.exec_start(i['Id'], detach=True, socket=True)
        mrtfile = '{0}/{1}'.format(self.host_dir, os.path.basename(conf['mrt-file']))
        shutil.copyfile(conf['mrt-file'], mrtfile)
        cmd = ['gobgp', 'mrt']
        if conf.get('only-best', False):
            cmd.append('--only-best')
        cmd += ['inject', 'global', '{0}/{1}'.format(self.guest_dir, os.path.basename(conf['mrt-file']))]
        if 'count' in conf:
            cmd.append(str(conf['count']))
        if 'skip' in conf:
            cmd.append(str(conf['skip']))
        i = dckr.exec_create(container=self.name, cmd=cmd)
        dckr.exec_start(i['Id'], detach=False, socket=True)

        config['neighbors'] = [{
            'config': {
                'neighbor-address': target['local-address'].split('/')[0],
                'peer-as': target['as'],
            },
        }]

        with open('{0}/{1}.conf'.format(self.host_dir, self.name), 'w') as f:
            f.write(yaml.dump(config, default_flow_style=False))

        i = dckr.exec_create(container=self.name, cmd=['pkill', '-SIGHUP', 'gobgpd'])
        dckr.exec_start(i['Id'], detach=False, socket=True)

        return ctn
