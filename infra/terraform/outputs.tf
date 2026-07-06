output "public_ip" {
  value = oci_core_instance.rs.public_ip
}

output "availability_domain" {
  value = oci_core_instance.rs.availability_domain
}
