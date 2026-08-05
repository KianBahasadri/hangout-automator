# Deploy

## Terraform (Azure VM)

Directory: `terraform/`. Requires Terraform ≥ 1.5 and AzureRM provider ~> 4.0.

Provisions roughly: resource group, VNet `10.20.0.0/16`, subnet `10.20.1.0/24`, static Standard public IP, NSG (SSH 22 from `allowed_ssh_cidr`, HTTP 80, app port **8000**), Ubuntu 22.04 LTS Gen2 VM (default size `Standard_B1s`, admin user `hangout`).

Notable variables (`variables.tf` / `terraform.tfvars.example`): `prefix`, `location` (default `eastus`), required `ssh_public_key`, optional `git_repo_url` / `git_branch` (default `main`), SMS/Twilio settings, `followup_hours`, `organizer_interval_hours`, `allowed_ssh_cidr`.

Outputs: resource group, public IP, `app_url` (`http://IP`), SSH command, `sms_webhook_url` (`http://IP/webhooks/sms`).

### cloud-init

Template `cloud-init.yaml.tftpl`:

- Installs Python, nginx, git
- Writes `/etc/hangout-automator.env` (app on `127.0.0.1:8000`, DB `sqlite:////var/lib/hangout-automator/app.db`, SMS settings from Terraform)
- systemd unit `hangout-automator.service` running Uvicorn
- nginx reverse-proxy 80 → 8000
- Bootstrap: clone `git_repo_url` (or leave a placeholder README), create venv, `pip install -r requirements.txt`, enable/restart nginx + service

Copy `terraform.tfvars.example` → `terraform.tfvars` (gitignored). Never commit real Twilio credentials or private keys.

## Rsync updates

`./scripts/deploy_rsync.sh user@host` syncs the project to `/opt/hangout-automator/` (excludes `.git`, `.venv`, DB files, `.env`, Terraform state), then remote venv install + `systemctl restart hangout-automator`.

Intended for code updates after the VM/cloud-init already exists; it does not recreate systemd/nginx units.
