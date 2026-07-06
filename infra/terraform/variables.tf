variable "tenancy_ocid" {
  type = string
}

variable "user_ocid" {
  type = string
}

variable "fingerprint" {
  description = "API signing key fingerprint."
  type        = string
}

variable "private_key_path" {
  description = "Path to the API signing private key."
  type        = string
}

variable "region" {
  type    = string
  default = "us-ashburn-1"
}

variable "compartment_ocid" {
  description = "Compartment for all resources (the tenancy root works for a personal account)."
  type        = string
}

variable "ssh_public_key" {
  description = "Public key installed for the ubuntu user."
  type        = string
}

variable "availability_domain_index" {
  description = "Which AD to try (rotate 0/1/2 when A1 capacity errors persist in one AD)."
  type        = number
  default     = 0
}

# The Always Free A1 allowance since 2026-06-15. PAYG accounts reportedly still get 4/24.
variable "ocpus" {
  type    = number
  default = 2
}

variable "memory_gbs" {
  type    = number
  default = 12
}

variable "boot_volume_gbs" {
  type    = number
  default = 100
}
