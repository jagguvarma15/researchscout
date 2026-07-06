resource "oci_core_vcn" "rs" {
  compartment_id = var.compartment_ocid
  display_name   = "researchscout"
  cidr_blocks    = ["10.0.0.0/16"]
  dns_label      = "rs"
}

resource "oci_core_internet_gateway" "rs" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.rs.id
  display_name   = "researchscout"
}

resource "oci_core_route_table" "rs" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.rs.id
  display_name   = "researchscout"

  route_rules {
    destination       = "0.0.0.0/0"
    network_entity_id = oci_core_internet_gateway.rs.id
  }
}

resource "oci_core_security_list" "rs" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.rs.id
  display_name   = "researchscout"

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
  }

  dynamic "ingress_security_rules" {
    for_each = [22, 80, 443]
    content {
      protocol = "6" # TCP
      source   = "0.0.0.0/0"
      tcp_options {
        min = ingress_security_rules.value
        max = ingress_security_rules.value
      }
    }
  }
}

resource "oci_core_subnet" "rs" {
  compartment_id    = var.compartment_ocid
  vcn_id            = oci_core_vcn.rs.id
  display_name      = "researchscout"
  cidr_block        = "10.0.1.0/24"
  route_table_id    = oci_core_route_table.rs.id
  security_list_ids = [oci_core_security_list.rs.id]
  dns_label         = "app"
}
