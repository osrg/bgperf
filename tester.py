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

from exabgp import ExaBGP
import os
from  settings import dckr

def rm_line():
    print '\x1b[1A\x1b[2K\x1b[1D\x1b[1A'


class Tester(ExaBGP):
    def get_ipv4_addresses(self):
        res = []
        peers = self.conf.get('tester', {}).values()
        for p in peers:
            res.append(p['local-address'].split('/')[0])
        return res

    def run(self, conf, target, brname=''):
        super(Tester, self).run(brname)

        startup = ['''#!/bin/bash
ulimit -n 65536''']

        peers = conf.get('tester', {}).values()

        for p in peers:
            with open('{0}/{1}.conf'.format(self.host_dir, p['router-id']), 'w') as f:
                local_address = p['local-address'].split('/')[0]
                config = '''neighbor {0} {{
    peer-as {1};
    router-id {2};
    local-address {3};
    local-as {4};
    static {{
'''.format(target['local-address'].split('/')[0], target['as'],
               p['router-id'], local_address, p['as'])
                f.write(config)
                for path in p['paths']:
                    f.write('      route {0} next-hop {1};\n'.format(path, local_address))
                f.write('''   }
}''')
            startup.append('''env exabgp.log.destination={0}/{1}.log \
exabgp.daemon.daemonize=true \
exabgp.daemon.user=root \
exabgp {0}/{1}.conf'''.format(self.guest_dir, p['router-id']))

        filename = '{0}/start.sh'.format(self.host_dir)
        with open(filename, 'w') as f:
            f.write('\n'.join(startup))
        os.chmod(filename, 0777)
        i = dckr.exec_create(container=self.name, cmd='{0}/start.sh'.format(self.guest_dir))
        cnt = 0
        for lines in dckr.exec_start(i['Id'], stream=True):
                for line in lines.strip().split('\n'):
                    cnt += 1
                    if cnt % 2 == 1:
                        if cnt > 1:
                            rm_line()
                        print 'tester booting.. ({0}/{1})'.format(cnt/2 + 1, len(conf.get('tester', {}).values()))
