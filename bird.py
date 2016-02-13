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

class BIRD(Container):
    def __init__(self, name, host_dir, guest_dir='/root/config', image='bgperf/bird'):
        super(BIRD, self).__init__(name, image, host_dir, guest_dir)

    @classmethod
    def build_image(cls, force=False, tag='bgperf/bird', checkout='HEAD', nocache=False):
        cls.dockerfile = '''
FROM ubuntu:latest
WORKDIR /root
RUN apt-get update && apt-get install -qy git autoconf libtool gawk make \
flex bison libncurses-dev libreadline6-dev
RUN apt-get install -qy flex
RUN git clone https://gitlab.labs.nic.cz/labs/bird.git bird && \
(cd bird && git checkout {0} && autoconf && ./configure && make && make install)
'''.format(checkout)
        super(BIRD, cls).build_image(force, tag, nocache)


    def write_config(self, conf, name='bird.conf'):
        config = '''router id {0};
listen bgp port 179;
protocol device {{ }}
protocol direct {{ disabled; }}
protocol kernel {{ disabled; }}
table master;
'''.format(conf['target']['router-id'])
        def gen_neighbor_config(n):
            return '''table table_{0};
protocol pipe pipe_{0} {{
    table master;
    mode transparent;
    peer table table_{0};
    import all;
    export all;
}}
protocol bgp bgp_{0} {{
    local as {1};
    neighbor {2} as {0};
    import all;
    export all;
    rs client;
}}
'''.format(n['as'], conf['target']['as'], n['local-address'].split('/')[0])

        with open('{0}/{1}'.format(self.host_dir, name), 'w') as f:
            f.write(config)
            for n in conf['tester'].values() + [conf['monitor']]:
                f.write(gen_neighbor_config(n))
        self.config_name = name


    def run(self, conf, brname=''):
        ctn = super(BIRD, self).run(brname)

        if self.config_name == None:
            self.write_config(conf)

        startup = '''#!/bin/bash
ulimit -n 65536
ip a add {0} dev eth1
bird -c {1}/{2}
'''.format(conf['target']['local-address'], self.guest_dir, self.config_name)
        filename = '{0}/start.sh'.format(self.host_dir)
        with open(filename, 'w') as f:
            f.write(startup)
        os.chmod(filename, 0777)
        i = dckr.exec_create(container=self.name, cmd='{0}/start.sh'.format(self.guest_dir))
        dckr.exec_inspect(i['Id'])
        dckr.exec_start(i['Id'], detach=True)
        return ctn
