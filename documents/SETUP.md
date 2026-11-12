# Employee Document Verification & OCR – Azure Setup Guide

This document contains the Azure CLI commands to create and configure all required resources for the Employee Document Verification & OCR project.

All commands are written for **PowerShell**.

## 1. Project Configuration

| Resource | Name |
|---|---|
| Resource Group | `rg-student-ai-korea` |
| Region | `koreacentral` |
| Storage Account | `ocrblobstorage2026` |
| Blob Container 1 | `processing-staging` |
| Blob Container 2 | `results` |
| Service Bus Namespace | `ocr-doc-processing-sb-2026` |
| Service Bus Queue | `document-processing` |
| Document Intelligence | `ocr-doc-intel-2026` |

## 2. Login and Configure Variables

```powershell
az login

$RG = "rg-student-ai-korea"
$LOCATION = "koreacentral"

$STORAGE = "ocrblobstorage2026"
$SB_NAMESPACE = "ocr-doc-processing-sb-2026"
$QUEUE = "document-processing"
$DI = "ocr-doc-intel-2026"
```

## 3. Create Resource Group

```powershell
az group create `
    --name $RG `
    --location $LOCATION
```

## 4. Create Azure Storage Account

```powershell
az storage account create `
    --name $STORAGE `
    --resource-group $RG `
    --location $LOCATION `
    --sku Standard_LRS `
    --kind StorageV2 `
    --access-tier Hot `
    --min-tls-version TLS1_2 `
    --allow-blob-public-access false `
    --https-only true
```

**Important:** Storage Account names must be globally unique. If the account already exists, do not create it again.

### Retrieve Storage Connection String

```powershell
$STORAGE_CONNECTION = az storage account show-connection-string `
    --name $STORAGE `
    --resource-group $RG `
    --query connectionString -o tsv

if ($LASTEXITCODE -ne 0 -or !$STORAGE_CONNECTION) {
    throw "Failed to retrieve Storage connection string."
}
```

### Retrieve a Fresh Storage Account Key

If you need to inspect or regenerate the connection string:

```powershell
$STORAGE_KEY = az storage account keys list `
    --account-name $STORAGE `
    --resource-group $RG `
    --query "[0].value" -o tsv
```

Use the current key when building or retrieving the connection string. If a key was regenerated, update every application using the old key.

## 5. Create Blob Containers

Create the application containers:

```powershell
$CONTAINERS = @(
    "processing-staging",
    "results"
)

foreach ($container in $CONTAINERS) {
    az storage container create `
        --name $container `
        --connection-string $STORAGE_CONNECTION `
        --public-access off
}
```

### Container Purpose

| Container | Purpose |
|---|---|
| `processing-staging` | Temporarily stores documents waiting for processing or verification. |
| `results` | Stores the combined OCR and document verification results. |

### Storage Authentication Notes

Azure Functions uses the `azure-webjobs-secrets` container internally to store host secrets.

This is separate from the two application containers.

The Functions host should create and manage its secrets container automatically. You do not normally need to create it manually.

If you receive a `403 AuthenticationFailed` or MAC signature mismatch:

1. Retrieve a fresh Storage connection string.
2. Verify that the account name and key belong to the same Storage Account.
3. Check whether Shared Key authentication is enabled.
4. Update `AzureWebJobsStorage` in `local.settings.json`.
5. Restart the Functions host.

Check Shared Key authentication:

```powershell
az storage account show `
    --name $STORAGE `
    --resource-group $RG `
    --query "{Name:name,AllowSharedKeyAccess:allowSharedKeyAccess,State:provisioningState}" `
    --output json
```

### Blob Lifecycle Management

Configure a lifecycle policy for `processing-staging` as a backup cleanup mechanism.

Use `staging-lifecycle-policy.json` and merge it with existing lifecycle rules rather than replacing them.

The application should delete staged files after successful extraction. Lifecycle cleanup is a delayed fallback for files left behind after failed runs.

If Blob soft delete is enabled, deleted files remain recoverable for the configured retention period.

## 6. Create Azure Service Bus Namespace

```powershell
az servicebus namespace create `
    --resource-group $RG `
    --name $SB_NAMESPACE `
    --location $LOCATION `
    --sku Standard
```

Standard is selected for the project's messaging requirements and supports the identity-based access configuration described later.

Check current Azure pricing before creating the namespace.

## 7. Create Service Bus Queue

```powershell
az servicebus queue create `
    --resource-group $RG `
    --namespace-name $SB_NAMESPACE `
    --name $QUEUE
```

### Retrieve Service Bus Connection String

For the current local development setup, use SAS connection-string authentication.

```powershell
$SERVICE_BUS_CONNECTION = az servicebus namespace authorization-rule keys list `
    --resource-group $RG `
    --namespace-name $SB_NAMESPACE `
    --name RootManageSharedAccessKey `
    --query primaryConnectionString `
    --output tsv

if ($LASTEXITCODE -ne 0 -or !$SERVICE_BUS_CONNECTION) {
    throw "Failed to retrieve Service Bus connection string."
}
```

A valid connection string looks like:

```text
Endpoint=sb://<namespace>.servicebus.windows.net/;SharedAccessKeyName=<policy>;SharedAccessKey=<key>
```

**Important:** Do not use the namespace URL alone as the connection string.

### Service Bus Authentication: Important

There are two supported configuration approaches.

**Option A – SAS connection string (current local setup)**

Use:

```json
{
    "ServiceBusConnection": "<complete-SAS-connection-string>"
}
```

Your Python trigger should reference:

```python
connection="ServiceBusConnection"
```

Do not add `ServiceBusConnection__fullyQualifiedNamespace` when using this configuration.

**Option B – Microsoft Entra ID (managed identity)**

For identity-based authentication, configure:

```text
ServiceBusConnection__fullyQualifiedNamespace=ocr-doc-processing-sb-2026.servicebus.windows.net
```

The application must use a compatible Azure Functions Service Bus extension and an identity with the appropriate RBAC permissions.

Do not mix the two authentication configurations.

### Optional: Assign Service Bus RBAC Roles

For identity-based access, assign the following roles to your signed-in developer:

```powershell
$SB_SCOPE = az servicebus namespace show `
    --resource-group $RG `
    --name $SB_NAMESPACE `
    --query id -o tsv

$QUEUE_SCOPE = "$SB_SCOPE/queues/$QUEUE"

$LOCAL_USER_ID = az ad signed-in-user show --query id -o tsv

az role assignment create `
    --assignee-object-id $LOCAL_USER_ID `
    --assignee-principal-type User `
    --role "Azure Service Bus Data Sender" `
    --scope $QUEUE_SCOPE

az role assignment create `
    --assignee-object-id $LOCAL_USER_ID `
    --assignee-principal-type User `
    --role "Azure Service Bus Data Receiver" `
    --scope $QUEUE_SCOPE
```

For a deployed Function App, enable managed identity and assign the required roles to its identity.

Only disable SAS/local authentication after every client has been migrated to Microsoft Entra ID.

## 8. Create Azure AI Document Intelligence

```powershell
az cognitiveservices account create `
    --name $DI `
    --resource-group $RG `
    --location $LOCATION `
    --kind FormRecognizer `
    --sku F0 `
    --yes
```

F0 is the free tier where available. If unavailable in your region or subscription, check the available SKUs before selecting S0, which is chargeable.

### Important: Recover a Soft-Deleted Resource

If you receive:

```text
FlagMustBeSetForRestore
```

It means an account with the same resource ID was previously deleted and is still soft-deleted.

Check the deleted resource:

```powershell
az cognitiveservices account show-deleted `
    --name $DI `
    --resource-group $RG `
    --location $LOCATION
```

Restore it instead of creating a new account:

```powershell
az cognitiveservices account recover `
    --name $DI `
    --resource-group $RG `
    --location $LOCATION
```

Verify:

```powershell
az cognitiveservices account show `
    --name $DI `
    --resource-group $RG `
    --query "{Name:name,State:properties.provisioningState}" `
    --output json
```

Do not purge the resource unless you intentionally want to permanently remove it.

### Retrieve Document Intelligence Endpoint

```powershell
$DI_ENDPOINT = az cognitiveservices account show `
    --name $DI `
    --resource-group $RG `
    --query properties.endpoint -o tsv
```

### Retrieve Document Intelligence Key

```powershell
$DI_KEY = az cognitiveservices account keys list `
    --name $DI `
    --resource-group $RG `
    --query key1 -o tsv
```

## 9. Configure Microsoft Foundry

The project uses an existing Microsoft Foundry project and agent.

```powershell
$FOUNDRY_Agent_Name = "DI-OCR-KR"

$FOUNDRY_PROJECT_ENDPOINT = "https://srivignesh648-9917-resource.services.ai.azure.com/api/projects/srivignesh648-9917"
```

These values should match the existing Foundry project and agent configuration.

## 10. Generate local.settings.json

Run this from your local Azure Functions project directory.

This configuration uses SAS connection-string authentication for local Service Bus development.

```powershell
$settings = @{
    IsEncrypted = $false
    Values = @{
        FUNCTIONS_WORKER_RUNTIME = "python"

        AzureWebJobsStorage = $STORAGE_CONNECTION
        StorageConnection = $STORAGE_CONNECTION

        ServiceBusConnection = $SERVICE_BUS_CONNECTION

        DI_ENDPOINT = $DI_ENDPOINT
        DI_KEY = $DI_KEY

        FOUNDRY_Agent_Name = $FOUNDRY_Agent_Name
        FOUNDRY_PROJECT_ENDPOINT = $FOUNDRY_PROJECT_ENDPOINT
    }
}

$settings | ConvertTo-Json -Depth 10 |
    Set-Content "local.settings.json" -Encoding utf8

Write-Host "local.settings.json created successfully."
```

### Expected local.settings.json Structure

```json
{
    "IsEncrypted": false,
    "Values": {
        "FUNCTIONS_WORKER_RUNTIME": "python",
        "AzureWebJobsStorage": "<storage-connection-string>",
        "StorageConnection": "<storage-connection-string>",
        "ServiceBusConnection": "<service-bus-connection-string>",
        "DI_ENDPOINT": "<document-intelligence-endpoint>",
        "DI_KEY": "<document-intelligence-key>",
        "FOUNDRY_Agent_Name": "DI-OCR-KR",
        "FOUNDRY_PROJECT_ENDPOINT": "<foundry-project-endpoint>"
    }
}
```

**Important configuration details:**

- `AzureWebJobsStorage` is required by the Functions host.
- `StorageConnection` is used by application code that accesses Blob Storage.
- `ServiceBusConnection` must contain the complete SAS connection string when using the current SAS trigger configuration.
- `DI_ENDPOINT` and `DI_KEY` connect to Document Intelligence.
- Foundry settings connect to the existing Foundry project and agent.

Never commit the real `local.settings.json` file to GitHub.

## 11. Verify All Azure Resources

### Verify Storage Account

```powershell
az storage account show `
    --resource-group $RG `
    --name $STORAGE `
    --query "{Name:name,State:provisioningState}" `
    --output table
```

### Verify Blob Containers

```powershell
az storage container list `
    --connection-string $STORAGE_CONNECTION `
    --query "[].name" `
    --output table
```

Expected:

```text
processing-staging
results
```

### Verify Service Bus Namespace

```powershell
az servicebus namespace show `
    --resource-group $RG `
    --name $SB_NAMESPACE `
    --query "{Name:name,State:provisioningState}" `
    --output table
```

### Verify Service Bus Queue

```powershell
az servicebus queue show `
    --resource-group $RG `
    --namespace-name $SB_NAMESPACE `
    --name $QUEUE `
    --query "{Name:name,Status:status}" `
    --output table
```

### Verify Document Intelligence

```powershell
az cognitiveservices account show `
    --resource-group $RG `
    --name $DI `
    --query "{Name:name,Endpoint:properties.endpoint,State:properties.provisioningState}" `
    --output json
```

## 12. Start the Local Azure Function

Navigate to your backend project directory:

```powershell
cd Backend
```

Activate your Python virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

Start Azure Functions:

```powershell
func start --verbose
```

Check that:

- The Functions host starts successfully.
- The Blob Storage secrets repository authenticates.
- The Service Bus listener starts successfully.
- The `start_run` function is loaded.
- The queue trigger is connected to `document-processing`.

If the Service Bus listener reports a connection-string parsing error, verify that `ServiceBusConnection` contains the full SAS connection string and that the Python trigger uses the same setting name.

## 13. Final Azure Resource Structure

```text
rg-student-ai-korea
│
├── ocrblobstorage2026
│   │
│   ├── processing-staging
│   │
│   ├── results
│   │
│   └── azure-webjobs-secrets
│       (Managed by Azure Functions)
│
├── ocr-doc-processing-sb-2026
│   │
│   └── document-processing
│
└── ocr-doc-intel-2026
    │
    └── Document Intelligence API
```

The Microsoft Foundry project and agent are existing resources configured separately.

**Note:** The Azure Function itself remains local for now. We will deploy it to an Azure Function App separately.

## 14. Git Security

Add the following to `.gitignore`:

```gitignore
local.settings.json
.env
.venv/
__pycache__/
*.pyc
.python_packages/
```

Never commit:

- Storage Account keys
- Service Bus connection strings
- Document Intelligence keys
- API keys
- Access tokens
- Passwords
- Local environment settings

For production, prefer Microsoft Entra ID, managed identities and Azure Key Vault where supported.

---

## 15. Setup Completion Checklist

- [ ] Resource Group created
- [ ] Storage Account created
- [ ] Blob containers created
- [ ] Storage connection verified
- [ ] Service Bus Namespace created
- [ ] Service Bus Queue created
- [ ] Service Bus connection verified
- [ ] Document Intelligence created or restored
- [ ] Document Intelligence endpoint retrieved
- [ ] Foundry project configuration added
- [ ] `local.settings.json` generated
- [ ] Azure Functions host started
- [ ] Blob Storage authentication verified
- [ ] Service Bus listener started successfully

**End of Azure Infrastructure Setup Guide.**