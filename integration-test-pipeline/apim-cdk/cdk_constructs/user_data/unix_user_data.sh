#cloud-boothook
#!/bin/bash

exec > >(tee /var/log/user-data.log|logger -t user-data -s 2>/dev/console) 2>&1
set -o verbose
set -o xtrace

# Set file limits
sysctl -w fs.file-max=2097152
sysctl fs.file-nr
ulimit -Hn
ulimit -Sn

if [[ "__OPERATING_SYSTEM__" == "Ubuntu" ]]; then
    export DEBIAN_FRONTEND=noninteractive
    apt update
    sleep 120
    dpkg --configure -a
    apt install -y zip unzip python3-pip jq python3-venv curl ca-certificates
    echo "Installing PostgreSQL/MySQL/MariaDB database clients"
    apt install -y postgresql-client mysql-client mariadb-client
    echo "Installing MSSQL database client"
    curl https://packages.microsoft.com/keys/microsoft.asc | tee /etc/apt/trusted.gpg.d/microsoft.asc
    curl https://packages.microsoft.com/config/ubuntu/26.04/prod.list | tee /etc/apt/sources.list.d/mssql-release.list
    apt update
    ACCEPT_EULA=Y apt install -y mssql-tools18 unixodbc-dev
    echo 'export PATH="/opt/mssql-tools18/bin:$PATH"' >> /etc/environment
    echo "Installing Amazon RDS global CA certificate"
    curl -fsSL https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem -o /usr/local/share/ca-certificates/rds-global-bundle.crt
    update-ca-certificates
fi
if [[ "__OPERATING_SYSTEM__" == "CentOS" ]] || [[ "__OPERATING_SYSTEM__" == "RHEL8" ]] || [[ "__OPERATING_SYSTEM__" == "RHEL9" ]] || [[ "__OPERATING_SYSTEM__" == "RHEL10" ]] || [[ "__OPERATING_SYSTEM__" == "Rocky" ]]; then
    yum install -y epel-release zip unzip jq wget git
    dnf update -y && dnf upgrade -y
    wget https://packages.microsoft.com/config/rhel/7/mssql-server-2017.repo -O /etc/yum.repos.d/mssql-server-2017.repo
    wget https://packages.microsoft.com/config/rhel/7/prod.repo -O /etc/yum.repos.d/msprod.repo
    ACCEPT_EULA=Y yum install -y mssql-tools unixODBC-devel
    if [[ "__OPERATING_SYSTEM__" == "RHEL10" ]]; then
        dnf install -y https://dev.mysql.com/get/mysql80-community-release-el9-1.noarch.rpm
        yum -y install mysql-community-client --nogpgcheck
    else
        yum -y install mysql
    fi
    echo 'export PATH="$PATH:/opt/mssql-tools/bin"' >> /etc/environment
    if [[ "__OPERATING_SYSTEM__" == "RHEL8" ]] || [[ "__OPERATING_SYSTEM__" == "RHEL9" ]] || [[ "__OPERATING_SYSTEM__" == "RHEL10" ]] || [[ "__OPERATING_SYSTEM__" == "Rocky" ]]; then
        dnf -y install postgresql
    fi
fi
if [[ "__OPERATING_SYSTEM__" == "SUSE" ]]; then
    zypper install -y zip unzip jq python-pip curl
fi

source /etc/environment

echo "Installing AWS CLI v2"
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip -o awscliv2.zip
./aws/install
AWS="/usr/local/bin/aws"
$AWS --version

mkdir -p /opt/testgrid/workspace
chmod 777 -R /opt/testgrid
cd /opt/testgrid/workspace

echo "Installing Apache Maven"
wget https://archive.apache.org/dist/maven/maven-3/__MAVEN_VERSION__/binaries/apache-maven-__MAVEN_VERSION__-bin.tar.gz
tar -xzf apache-maven-__MAVEN_VERSION__-bin.tar.gz
ln -fs apache-maven-__MAVEN_VERSION__ maven
echo 'export MAVEN_OPTS="-Xmx4096m -Xms2048m"' >> /etc/environment
echo 'export M3_HOME=/opt/testgrid/workspace/maven' >> /etc/environment
echo PATH=/opt/testgrid/workspace/maven/bin/:$PATH >> /etc/environment
source /etc/environment

cat /dev/null > ~/.bash_history && history -c
__CUSTOM_USER_DATA__

WORKING_DIR=$(pwd)

# Instance credentials come from the attached IAM role -- no access keys are
# baked into user-data, unlike the source CloudFormation template.
echo "Fetching database credentials from Secrets Manager"
DB_SECRET_JSON=$($AWS secretsmanager get-secret-value --secret-id "__DB_SECRET_ARN__" --query SecretString --output text --region "__AWS_REGION__")
DB_USERNAME=$(echo "$DB_SECRET_JSON" | jq -r .username)
DB_PASSWORD=$(echo "$DB_SECRET_JSON" | jq -r .password)

# write property file
echo "DB_TYPE=__DB_TYPE__" > cfn-props.properties
echo "CF_DB_VERSION=__DB_VERSION__" >> cfn-props.properties
echo "JDK_TYPE=__JDK__" >> cfn-props.properties
echo "CF_DB_PASSWORD=$DB_PASSWORD" >> cfn-props.properties
echo "CF_DB_USERNAME=$DB_USERNAME" >> cfn-props.properties
echo "CF_DB_HOST=__DB_HOST__" >> cfn-props.properties
echo "CF_DB_PORT=__DB_PORT__" >> cfn-props.properties
echo "SID=__DB_NAME__" >> cfn-props.properties
echo "REMOTE_PACK_NAME=__PRODUCT__-__PRODUCT_VERSION__" >> cfn-props.properties

$AWS s3 cp s3://wum-for-testgrid/uat-config.json "$WORKING_DIR/uat-config.json"
$AWS s3 cp s3://wum-for-testgrid/uat-nexus-settings.xml "$WORKING_DIR/uat-nexus-settings.xml"
echo "Downloading vanilla product __PRODUCT__-__PRODUCT_VERSION__ from AWS S3 completed successfully"
$AWS s3 cp "s3://wum-for-testgrid/packs/__PRODUCT__-__PRODUCT_VERSION__.zip" "$WORKING_DIR"

if [[ "__OPERATING_SYSTEM__" == "Ubuntu" ]]; then
    echo "Downloading authorized public keys from AWS S3"
    $AWS s3 cp s3://integration-testgrid-resources/authorized-public-keys/authorized_keys /home/ubuntu/.ssh/authorized_keys
    chmod 644 /home/ubuntu/.ssh/authorized_keys
    chown ubuntu:ubuntu /home/ubuntu/.ssh/authorized_keys
fi

DB_PROV_SCRIPT_NAME=provision_db___PRODUCT__.sh
wget "https://raw.githubusercontent.com/wso2/testgrid/master/jobs/intg-test-resources/update2-releases/wso2-update.sh"
chmod +x /opt/testgrid/workspace/wso2-update.sh
wget "https://integration-testgrid-resources.s3.amazonaws.com/db_scripts/$DB_PROV_SCRIPT_NAME"
chmod +x "$DB_PROV_SCRIPT_NAME"

echo "Database configs: __DB_TYPE__ __DB_VERSION__"
sed -i "s/&CF_DB_USERNAME/$DB_USERNAME/g" "$DB_PROV_SCRIPT_NAME"
sed -i "s/&CF_DB_PASSWORD/$DB_PASSWORD/g" "$DB_PROV_SCRIPT_NAME"
sed -i "s/&CF_DB_HOST/__DB_HOST__/g" "$DB_PROV_SCRIPT_NAME"
sed -i "s/&CF_DB_PORT/__DB_PORT__/g" "$DB_PROV_SCRIPT_NAME"
sed -i "s/&CF_DB_NAME/__DB_TYPE__/g" "$DB_PROV_SCRIPT_NAME"
sed -i "s/&CF_DB_VERSION/__DB_VERSION__/g" "$DB_PROV_SCRIPT_NAME"
sed -i "s/&PRODUCT_VERSION/__PRODUCT_VERSION__/g" "$DB_PROV_SCRIPT_NAME"
