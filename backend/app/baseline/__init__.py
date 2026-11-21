"""Configuration baseline of ESXi hosts, clusters and VMs (ICO v2 G9a). Facts are read in the vCenter inventory session
(vcenter/config_facts.py, read-only), stored per vCenter (cfg_fact_state) and evaluated on read against the rules
(rules.py defaults + cfg_rule overrides) minus the exceptions (cfg_exception): changing a rule takes effect at once."""
