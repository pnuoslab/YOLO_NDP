# max io size(-i) 131072
sudo scripts/rpc.py nvmf_create_transport -t TCP -u 131072 -m 20 -c 8192 -i 2097152
sudo scripts/rpc.py bdev_malloc_create -b Malloc0 1024 4096
sudo scripts/rpc.py nvmf_create_subsystem nqn.2016-06.io.spdk:cnode1 -a -s SPDK00000000000001 -d /dev/nvme0n1
sudo scripts/rpc.py nvmf_subsystem_add_ns nqn.2016-06.io.spdk:cnode1 Malloc0
sudo scripts/rpc.py nvmf_subsystem_add_listener nqn.2016-06.io.spdk:cnode1 -t tcp -a 192.168.0.5 -s 4420
