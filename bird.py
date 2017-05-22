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
    def __init__(self, name, host_dir, conf, guest_dir='/root/config', image='bgperf/bird'):
        super(BIRD, self).__init__(name, image, host_dir, guest_dir, conf)

    @classmethod
    def build_image(cls, force=False, tag='bgperf/bird', checkout='HEAD', nocache=False):
        cls.dockerfile = '''
FROM ubuntu:latest
WORKDIR /root
RUN apt-get update && apt-get install -qy git autoconf libtool gawk make \
flex bison libncurses-dev libreadline6-dev
RUN apt-get install -qy flex
RUN git clone https://gitlab.labs.nic.cz/labs/bird.git bird
RUN cd bird && git checkout {0} && autoreconf -i && ./configure && make && make install
'''.format(checkout)
        super(BIRD, cls).build_image(force, tag, nocache)


    def write_config(self, conf, name='bird.conf'):
        if self.use_existing_config(name):
            return

        config = '''router id {0};
listen bgp port 179;
protocol device {{ }}
protocol direct {{ disabled; }}
protocol kernel {{ disabled; }}
table master{1};
'''.format(conf['target']['router-id'], ' sorted' if conf['target']['single-table'] else '')

        def gen_filter_assignment(n):
            if 'filter' in n:
                c = []
                if 'in' not in n['filter'] or len(n['filter']['in']) == 0:
                    c.append('import all;')
                else:
                    c.append('import where {0};'.format( '&&'.join(x + '()' for x in n['filter']['in'])))

                if 'out' not in n['filter'] or len(n['filter']['out']) == 0:
                    c.append('export all;')
                else:
                    c.append('export where {0};'.format( '&&'.join(x + '()' for x in n['filter']['out'])))

                return '\n'.join(c)
            return '''import all;
export all;
'''

        def gen_neighbor_config(n):
            return ('''table table_{0};
protocol pipe pipe_{0} {{
    table master;
    mode transparent;
    peer table table_{0};
{1}
}}'''.format(n['as'], gen_filter_assignment(n)) if not conf['target']['single-table'] else '') + '''protocol bgp bgp_{0} {{
    local as {1};
    neighbor {2} as {0};
    {3};
    import all;
    export all;
    rs client;
}}
'''.format(n['as'], conf['target']['as'], n['local-address'].split('/')[0], 'secondary' if conf['target']['single-table'] else 'table table_{0}'.format(n['as']))
            return n1 + n2

        def gen_prefix_filter(name, match):
            return '''function {0}()
prefix set prefixes;
{{
prefixes = [
{1}
];
if net ~ prefixes then return false;
return true;
}}
'''.format(name, ',\n'.join(match['value']))

        def gen_aspath_filter(name, match):
            c = '''function {0}()
{{
'''.format(name)
            c += '\n'.join('if (bgp_path ~ [= * {0} * =]) then return false;'.format(v) for v in match['value'])
            c += '''
return true;
}
'''
            return c

        def gen_community_filter(name, match):
            c = '''function {0}()
{{
'''.format(name)
            c += '\n'.join('if ({0}, {1}) ~ bgp_community then return false;'.format(*v.split(':')) for v in match['value'])
            c += '''
return true;
}
'''
            return c

        def gen_ext_community_filter(name, match):
            c = '''function {0}()
{{
'''.format(name)
            c += '\n'.join('if ({0}, {1}, {2}) ~ bgp_ext_community then return false;'.format(*v.split(':')) for v in match['value'])
            c += '''
return true;
}
'''
            return c



        def gen_filter(name, match):
            c = ['function {0}()'.format(name), '{']
            for typ, name in match:
                c.append(' if ! {0}() then return false;'.format(name))
            c.append('return true;')
            c.append('}')
            return '\n'.join(c) + '\n'

        with open('{0}/{1}'.format(self.host_dir, name), 'w') as f:
            f.write(config)

            if 'policy' in conf:
                for k, v in conf['policy'].iteritems():
                    match_info = []
                    for i, match in enumerate(v['match']):
                        n = '{0}_match_{1}'.format(k, i)
                        if match['type'] == 'prefix':
                            f.write(gen_prefix_filter(n, match))
                        elif match['type'] == 'as-path':
                            f.write(gen_aspath_filter(n, match))
                        elif match['type'] == 'community':
                            f.write(gen_community_filter(n, match))
                        elif match['type'] == 'ext-community':
                            f.write(gen_ext_community_filter(n, match))
                        match_info.append((match['type'], n))
                    f.write(gen_filter(k, match_info))

            for n in sorted(list(flatten(t.get('tester', {}).values() for t in conf['testers'])) + [conf['monitor']], key=lambda n: n['as']):
                f.write(gen_neighbor_config(n))
            f.flush()
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
        dckr.exec_start(i['Id'], detach=True, socket=True)
        return ctn
