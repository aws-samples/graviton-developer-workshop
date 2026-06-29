# Migrating Amazon Aurora to Graviton — Lab Guide

This lab walks you through migrating an Amazon Aurora PostgreSQL cluster from x86 (Intel/AMD) to AWS Graviton processors. Aurora's shared storage architecture makes this particularly smooth — you can add a Graviton Reader, validate, then failover with seconds of downtime and an instant rollback path.

## Why Graviton for Aurora?

- **~20% cost savings** — Graviton instances are cheaper at the same vCPU/memory tier
- **No data movement** — Aurora's shared storage is decoupled from compute
- **Instant rollback** — failover back to the x86 instance if anything goes wrong
- **Mixed fleet** — run x86 Writer + Graviton Readers during validation
- **No application changes** — same cluster endpoint, same protocol

## Instance Family Mapping

| x86 Instance | Graviton Equivalent | Type |
|---|---|---|
| `db.r6i.large` | `db.r7g.large` | Memory optimized |
| `db.r5.large` | `db.r7g.large` | Memory optimized |
| `db.r6i.xlarge` | `db.r7g.xlarge` | Memory optimized |
| `db.t3.medium` | `db.t4g.medium` | Burstable |

## Prerequisites

- AWS CLI v2 configured with appropriate credentials
- An EC2 instance (Amazon Linux 2023) in the same VPC as the RDS cluster
- Permissions to create/modify Aurora clusters

### Install psql client

```bash
sudo dnf install -y postgresql16
psql --version
```

## Lab Modules

| Module | Topic | Time |
|--------|-------|------|
| 1 | [Create an Aurora PostgreSQL cluster (x86)](#module-1-create-an-aurora-postgresql-cluster-x86) | 10 min |
| 2 | [Load sample data](#module-2-load-sample-data) | 5 min |
| 3 | [Add a Graviton Reader](#module-3-add-a-graviton-reader) | 10 min |
| 4 | [Failover to Graviton (promote Reader to Writer)](#module-4-failover-to-graviton) | 5 min |
| 5 | [Verify data integrity post-migration](#module-5-verify-data-integrity-post-migration) | 5 min |
| 6 | [Cleanup old x86 instance](#module-6-cleanup-old-x86-instance) | 5 min |
| 7 | [Teardown](#module-7-teardown) | 5 min |

---

## Module 1: Create an Aurora PostgreSQL cluster (x86)

### 1.1 Set environment variables

```bash
export AWS_REGION="us-east-1"
export CLUSTER_ID="graviton-lab-aurora"
export WRITER_ID="${CLUSTER_ID}-writer"
export DB_ENGINE="aurora-postgresql"
export DB_ENGINE_VERSION="16.6"
export INSTANCE_CLASS="db.r6i.large"  # x86
export DB_MASTER_USER="labadmin"
export DB_MASTER_PASSWORD="$(openssl rand -base64 16 | tr -dc 'A-Za-z0-9' | head -c 20)"
echo "Password: $DB_MASTER_PASSWORD"  # Save this
```

### 1.2 Create a DB subnet group

```bash
SUBNETS=$(aws ec2 describe-subnets \
  --filters "Name=default-for-az,Values=true" \
  --query 'Subnets[*].SubnetId' --output text | tr '\t' ' ')

aws rds create-db-subnet-group \
  --db-subnet-group-name graviton-lab-subnets \
  --db-subnet-group-description "Graviton migration lab" \
  --subnet-ids $SUBNETS
```

### 1.3 Create a security group for Aurora

```bash
VPC_ID=$(aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" \
  --query 'Vpcs[0].VpcId' --output text)

DB_SG=$(aws ec2 create-security-group \
  --group-name graviton-lab-aurora-sg \
  --description "Aurora access from EC2" \
  --vpc-id $VPC_ID \
  --query 'GroupId' --output text)

# Allow PostgreSQL access from this EC2 instance's security group
TOKEN=$(curl -sX PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
EC2_SG_ID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/network/interfaces/macs/$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/mac)/security-group-ids | head -1)

aws ec2 authorize-security-group-ingress \
  --group-id $DB_SG \
  --protocol tcp --port 5432 \
  --source-group $EC2_SG_ID

echo "Aurora SG: $DB_SG (allows port 5432 from $EC2_SG_ID)"
```

### 1.4 Create the Aurora cluster

```bash
aws rds create-db-cluster \
  --db-cluster-identifier $CLUSTER_ID \
  --engine $DB_ENGINE \
  --engine-version $DB_ENGINE_VERSION \
  --master-username $DB_MASTER_USER \
  --master-user-password $DB_MASTER_PASSWORD \
  --db-subnet-group-name graviton-lab-subnets \
  --vpc-security-group-ids $DB_SG \
  --tags Key=lab,Value=graviton-migration
```

### 1.5 Create the Writer instance (x86)

```bash
aws rds create-db-instance \
  --db-instance-identifier $WRITER_ID \
  --db-cluster-identifier $CLUSTER_ID \
  --db-instance-class $INSTANCE_CLASS \
  --engine $DB_ENGINE
```

### 1.6 Wait for the cluster to be available

```bash
aws rds wait db-instance-available --db-instance-identifier $WRITER_ID
echo "Aurora cluster is ready"
```

### 1.7 Confirm the setup

```bash
aws rds describe-db-clusters \
  --db-cluster-identifier $CLUSTER_ID \
  --query 'DBClusters[0].{Endpoint:Endpoint,ReaderEndpoint:ReaderEndpoint,Engine:Engine,EngineVersion:EngineVersion}' \
  --output table

aws rds describe-db-instances \
  --db-instance-identifier $WRITER_ID \
  --query 'DBInstances[0].{Class:DBInstanceClass,Role:DBInstanceStatus}' \
  --output table
```

Expected: `db.r6i.large` (x86) Writer.

---

## Module 2: Load sample data

### 2.1 Connect to the cluster

```bash
export PGHOST=$(aws rds describe-db-clusters \
  --db-cluster-identifier $CLUSTER_ID \
  --query 'DBClusters[0].Endpoint' --output text)
export PGUSER=$DB_MASTER_USER
export PGPASSWORD=$DB_MASTER_PASSWORD
export PGDATABASE=postgres

echo "Writer endpoint: $PGHOST"
```

### 2.2 Create a sample database and tables

```bash
psql <<'SQL'
CREATE DATABASE sampledb;
\c sampledb

CREATE TABLE customers (
  id SERIAL PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  email VARCHAR(255) UNIQUE NOT NULL,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE orders (
  id SERIAL PRIMARY KEY,
  customer_id INT REFERENCES customers(id),
  amount DECIMAL(10,2) NOT NULL,
  status VARCHAR(20) DEFAULT 'pending',
  created_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO customers (name, email)
SELECT
  'Customer ' || i,
  'customer' || i || '@example.com'
FROM generate_series(1, 1000) AS i;

INSERT INTO orders (customer_id, amount, status)
SELECT
  (random() * 999 + 1)::int,
  (random() * 500 + 10)::decimal(10,2),
  CASE (random() * 3)::int
    WHEN 0 THEN 'pending'
    WHEN 1 THEN 'shipped'
    ELSE 'delivered'
  END
FROM generate_series(1, 5000);

CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_orders_status ON orders(status);
SQL
```

### 2.3 Verify the data and save a baseline

```bash
psql -d sampledb -c "SELECT count(*) AS customers FROM customers;"
psql -d sampledb -c "SELECT count(*) AS orders FROM orders;"
psql -d sampledb -c "SELECT status, count(*), round(avg(amount),2) AS avg_amount FROM orders GROUP BY status ORDER BY status;"
```

### 2.4 Confirm architecture (x86)

```bash
psql -d sampledb -c "SELECT version();"
# Expected: PostgreSQL 16.x on x86_64-pc-linux-gnu
```

---

## Module 3: Add a Graviton Reader

Aurora's shared storage means a new Reader instance sees the same data instantly — no replication lag during setup.

### 3.1 Create a Graviton Reader

```bash
export GRAVITON_READER_ID="${CLUSTER_ID}-graviton"
export GRAVITON_INSTANCE_CLASS="db.r7g.large"

aws rds create-db-instance \
  --db-instance-identifier $GRAVITON_READER_ID \
  --db-cluster-identifier $CLUSTER_ID \
  --db-instance-class $GRAVITON_INSTANCE_CLASS \
  --engine $DB_ENGINE
```

### 3.2 Wait for the Reader to be available

```bash
aws rds wait db-instance-available --db-instance-identifier $GRAVITON_READER_ID
echo "Graviton Reader is ready"
```

### 3.3 Verify the cluster now has both instances

```bash
aws rds describe-db-clusters \
  --db-cluster-identifier $CLUSTER_ID \
  --query 'DBClusters[0].DBClusterMembers[*].{Instance:DBInstanceIdentifier,IsWriter:IsClusterWriter}' \
  --output table
```

Expected:
| Instance | IsWriter |
|----------|----------|
| `graviton-lab-aurora-writer` | True |
| `graviton-lab-aurora-graviton` | False |

### 3.4 Test the Graviton Reader

```bash
READER_ENDPOINT=$(aws rds describe-db-instances \
  --db-instance-identifier $GRAVITON_READER_ID \
  --query 'DBInstances[0].Endpoint.Address' --output text)

PGHOST=$READER_ENDPOINT psql -d sampledb -c "SELECT version();"
# Expected: PostgreSQL 16.x on aarch64-unknown-linux-gnu
#                              ^^^^^^^ Graviton!

PGHOST=$READER_ENDPOINT psql -d sampledb -c "SELECT count(*) FROM orders;"
# Expected: 5000 — same data, shared storage
```

At this point you have a **mixed fleet** — x86 Writer handling writes, Graviton Reader serving reads. This is a safe validation state.

---

## Module 4: Failover to Graviton

Promote the Graviton Reader to Writer. This causes seconds of downtime while the cluster endpoint switches.

### 4.1 Failover the cluster

```bash
aws rds failover-db-cluster \
  --db-cluster-identifier $CLUSTER_ID \
  --target-db-instance-identifier $GRAVITON_READER_ID
```

### 4.2 Wait for the failover to complete

```bash
aws rds wait db-instance-available --db-instance-identifier $GRAVITON_READER_ID
```

### 4.3 Confirm the new Writer is Graviton

```bash
aws rds describe-db-clusters \
  --db-cluster-identifier $CLUSTER_ID \
  --query 'DBClusters[0].DBClusterMembers[*].{Instance:DBInstanceIdentifier,IsWriter:IsClusterWriter}' \
  --output table
```

Expected:
| Instance | IsWriter |
|----------|----------|
| `graviton-lab-aurora-graviton` | **True** |
| `graviton-lab-aurora-writer` | False |

The Graviton instance is now the Writer. The old x86 instance is now a Reader.

> **Rollback:** If anything goes wrong, simply run the failover again targeting the old x86 instance:
> ```bash
> aws rds failover-db-cluster \
>   --db-cluster-identifier $CLUSTER_ID \
>   --target-db-instance-identifier $WRITER_ID
> ```

---

## Module 5: Verify data integrity post-migration

### 5.1 Confirm architecture

```bash
export PGHOST=$(aws rds describe-db-clusters \
  --db-cluster-identifier $CLUSTER_ID \
  --query 'DBClusters[0].Endpoint' --output text)

psql -d sampledb -c "SELECT version();"
# Expected: PostgreSQL 16.x on aarch64-unknown-linux-gnu
```

### 5.2 Verify row counts

```bash
psql -d sampledb -c "SELECT 'customers' AS table_name, count(*) FROM customers
                      UNION ALL
                      SELECT 'orders', count(*) FROM orders;"
```

Expected: 1,000 customers and 5,000 orders — unchanged.

### 5.3 Verify data correctness

```bash
psql -d sampledb -c "SELECT status, count(*), round(avg(amount),2) AS avg_amount FROM orders GROUP BY status ORDER BY status;"
```

Compare with Module 2.3 output — values should be identical.

### 5.4 Verify indexes and constraints

```bash
psql -d sampledb -c "\di"
psql -d sampledb -c "SELECT conname, contype FROM pg_constraint WHERE conrelid = 'orders'::regclass;"
```

### 5.5 Test a write operation

```bash
psql -d sampledb <<'SQL'
INSERT INTO customers (name, email) VALUES ('Post-Migration Test', 'test@graviton.example');
INSERT INTO orders (customer_id, amount, status)
  SELECT id, 99.99, 'delivered' FROM customers WHERE email = 'test@graviton.example';
SELECT c.name, o.amount, o.status
  FROM customers c JOIN orders o ON o.customer_id = c.id
  WHERE c.email = 'test@graviton.example';
SQL
```

Expected: reads and writes work on the Graviton Writer.

---

## Module 6: Cleanup old x86 instance

Once validated, remove the old x86 instance (now a Reader) to stop paying for it.

### 6.1 Delete the old x86 Reader

```bash
aws rds delete-db-instance \
  --db-instance-identifier $WRITER_ID \
  --skip-final-snapshot

aws rds wait db-instance-deleted --db-instance-identifier $WRITER_ID
echo "Old x86 instance removed"
```

### 6.2 Confirm the cluster

```bash
aws rds describe-db-clusters \
  --db-cluster-identifier $CLUSTER_ID \
  --query 'DBClusters[0].DBClusterMembers[*].{Instance:DBInstanceIdentifier,IsWriter:IsClusterWriter,Class:DBInstanceClass}' \
  --output table
```

Your cluster now runs entirely on Graviton.

---

## Module 7: Teardown

```bash
# Delete the remaining Graviton instance
aws rds delete-db-instance \
  --db-instance-identifier $GRAVITON_READER_ID \
  --skip-final-snapshot

aws rds wait db-instance-deleted --db-instance-identifier $GRAVITON_READER_ID

# Delete the cluster
aws rds delete-db-cluster \
  --db-cluster-identifier $CLUSTER_ID \
  --skip-final-snapshot

# Delete the subnet group
aws rds delete-db-subnet-group --db-subnet-group-name graviton-lab-subnets

# Delete the security group
aws ec2 delete-security-group --group-id $DB_SG

echo "Cleanup complete"
```

---

## Key Takeaways

| Aspect | Detail |
|--------|--------|
| Downtime | Seconds (failover-based) |
| Data migration | None — Aurora shared storage |
| Rollback | Instant — failover back to x86 instance |
| Validation | Run mixed fleet (x86 Writer + Graviton Reader) before promoting |
| Application changes | None — cluster endpoint stays the same |
| Cost savings | ~20% at same vCPU/memory tier |

## Migration Steps Summary

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  1. Add Graviton │     │  2. Validate on  │     │  3. Failover to  │
│     Reader       │ ──► │     Reader       │ ──► │     Graviton     │
│  (db.r7g.large)  │     │  (shared storage)│     │  (seconds)       │
└──────────────────┘     └──────────────────┘     └──────────────────┘
                                                           │
                                                           ▼
                                                  ┌──────────────────┐
                                                  │  4. Remove old   │
                                                  │     x86 instance │
                                                  └──────────────────┘
```

## Additional Resources

- [Aurora PostgreSQL instance classes](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Concepts.DBInstanceClass.html)
- [Aurora failover](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Concepts.AuroraHighAvailability.html)
- [Graviton Getting Started — Managed Services](https://github.com/aws/aws-graviton-getting-started/blob/main/managed_services.md)
