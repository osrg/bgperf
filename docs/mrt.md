# MRT injection

This feature requires the basic knowledge about how `bgperf` works.
Please refer to [this guide](https://github.com/osrg/bgperf/blob/master/docs/how_bgperf_works.md).

`bgperf` supports injecting routes to the target implementation via MRT file.
With this feature, you can inject more realistic routes rather than artifitial routes which
`bgperf` automatically generates.

To use the feature, you need to create your own `scenario.yaml`.

Below is an example configuration to enable MRT injection feature.

```shell
$ cat /tmp/bgperf/scenario.yaml
<%
    import netaddr
    from itertools import islice

    it = netaddr.iter_iprange('100.0.0.0','160.0.0.0')

    def gen_paths(num):
        return list('{0}/32'.format(ip) for ip in islice(it, num))
%>
local_prefix: 10.10.0.0/24
monitor:
  as: 1001
  check-points: [1000]
  local-address: 10.10.0.2
  router-id: 10.10.0.2
target: {as: 1000, local-address: 10.10.0.1, router-id: 10.10.0.1}
testers:
- name: mrt-injector
  type: mrt
  neighbors:
    10.10.0.200:
      as: 1200
      local-address: 10.10.0.200
      router-id: 10.10.0.200
      mrt-file: /path/to/mrt/file
      only-best: true # only inject best path to the tester router (recommended to set this true)
      count: 1000 # number of routes to inject
      skip: 100 # number of routers to skip in the mrt file
- name: tester
  neighbors:
    10.10.0.10:
      as: 1010
      local-address: 10.10.0.10
      paths: ${gen_paths(100)}
      router-id: 10.10.0.10
    10.10.0.100:
      as: 1100
      local-address: 10.10.0.100
      paths: ${gen_paths(100)}
      router-id: 10.10.0.100
```

By adding `type: mrt`, tester will be run in mrt mode.
With this configuration, the mrt tester will inject 1000 routes taken from the
MRT file with 100 offset to the target router.

As you can see, you can mix normal tester and mrt tester to create more
complicated scenario.
