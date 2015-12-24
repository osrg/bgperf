#!/usr/bin/env python
#
# Copyright (C) 2015 Nippon Telegraph and Telephone Corporation.
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

import yaml
from argparse import ArgumentParser

def main():
    parser = ArgumentParser(description='Generate BGPerf configuration file.')
    parser.add_argument('-t', '--target-type', default='gobgp', type=str)
    parser.add_argument('-n', '--num-peer', default=10, type=int)
    parser.add_argument('-p', '--num-prefix', default=10, type=int)
    parser.add_argument('-i', '--identical', action='store_true')
    parser.add_argument('-o', '--output', default='bgperf.yml', type=str)
    args = parser.parse_args()

    conf = {}
    conf['target'] = {
        'type': args.target_type,
        'as': 1000,
        'router-id': '10.10.0.1',
        'local-address': '10.10.0.1/16',
        'docker': {
            'name': 'target',
        },
    }
    conf['tester'] = {}
    for i in range(2, args.num_peer+2):
        router_id = '10.10.{0}.{1}'.format(i/255, i%255)
        paths = []
        for j in range(args.num_prefix):
            paths.append('{0}.{1}.{2}.{3}/32'.format(i%254 + 1, i/254 + 1, j/255, j%255))

        conf['tester'][router_id] = {
            'as': 1000 + i,
            'router-id': router_id,
            'local-address': router_id + '/16',
            'paths': paths,
        }

    with open(args.output, 'w') as f:
        f.write(yaml.dump(conf))


if __name__ == '__main__':
    main()
