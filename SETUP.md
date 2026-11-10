Below are the Azure CLI commands to create all required resources from scratch for your Employee Document Verification & OCR project.

Using your selected configuration:

- Resource group: `rg-student-ai-korea`
- Region: `koreacentral`
- Storage: `ocrblobstorage`
- Containers: `prev`, `to-verify`, `results`
- Service Bus: `ocr-doc-processing-sb-2026`
- Queue: `document-processing`
- Document Intelligence: `ocr-doc-intel-2026`

Run these in PowerShell, in order.

## 1. Login and configure variables

```
az login

$RG = "rg-student-ai-korea"
$LOCATION = "koreacentral"

$STORAGE = "ocrblobstorage2026"
$SB_NAMESPACE = "ocr-doc-processing-sb-2026"
$QUEUE = "document-processing"
$DI = "ocr-doc-intel-2026"
```

## 2. Create Resource Group

```
az group create `
    --name $RG `
    --location $LOCATION
```

## 3. Create Storage Account

```
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

## 4. Create Blob Containers

```
$STORAGE_CONNECTION = az storage account show-connection-string `
    --name $STORAGE `
    --resource-group $RG `
    --query connectionString -o tsv

$CONTAINERS = @("prev", "to-verify", "results")

foreach ($container in $CONTAINERS) {
    az storage container create `
        --name $container `
        --connection-string $STORAGE_CONNECTION `
        --public-access off
}
```

## 5. Create Azure Service Bus Namespace

```
az servicebus namespace create `
    --resource-group $RG `
    --name $SB_NAMESPACE `
    --location $LOCATION `
    --sku Standard
```

Standard is required for the identity-based Service Bus access configured below. It may cost more than Basic; check your subscription's current pricing before creating the namespace.

## 6. Create Service Bus Queue

```
az servicebus queue create `
    --resource-group $RG `
    --namespace-name $SB_NAMESPACE `
    --name $QUEUE
```

## 7. Grant queue access with Microsoft Entra ID

```
$SB_SCOPE = az servicebus namespace show `
    --resource-group $RG `
    --namespace-name $SB_NAMESPACE `
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

For a deployed Function App, enable its managed identity and assign the same two roles to its service principal on the queue. The two role assignments above let the signed-in developer send and receive messages locally without storing a Service Bus key. After confirming every client uses Microsoft Entra ID, disable local/SAS authentication on the namespace.

## 8. Create Azure AI Document Intelligence

```
az cognitiveservices account create `
    --name $DI `
    --resource-group $RG `
    --location $LOCATION `
    --kind FormRecognizer `
    --sku F0 `
    --yes
```

Retrieve endpoint:

```
$DI_ENDPOINT = az cognitiveservices account show `
    --name $DI `
    --resource-group $RG `
    --query properties.endpoint -o tsv
```

Retrieve key:

```
$DI_KEY = az cognitiveservices account keys list `
    --name $DI `
    --resource-group $RG `
    --query key1 -o tsv
```

If F0 is unavailable in your region or subscription, check available SKUs before choosing S0, which is chargeable.

## 9. Generate `local.settings.json`

Run this inside your local Azure Function project directory:

```
$settings = @{
    IsEncrypted = $false
    Values = @{
        FUNCTIONS_WORKER_RUNTIME = "python"
        AzureWebJobsStorage = $STORAGE_CONNECTION
        StorageConnection = $STORAGE_CONNECTION
        ServiceBusConnection__fullyQualifiedNamespace = "$SB_NAMESPACE.servicebus.windows.net"
        DI_ENDPOINT = $DI_ENDPOINT
        DI_KEY = $DI_KEY
    }
}

$settings | ConvertTo-Json -Depth 5 |
    Set-Content "local.settings.json" -Encoding utf8
```

## 10. Verify all resources

```
az storage account show -g $RG -n $STORAGE --query name -o tsv

az storage container list `
    --connection-string $STORAGE_CONNECTION `
    --query "[].name" -o table

az servicebus namespace show -g $RG -n $SB_NAMESPACE --query name -o tsv

az servicebus queue show `
    -g $RG `
    --namespace-name $SB_NAMESPACE `
    -n $QUEUE `
    --query name -o tsv

az cognitiveservices account show `
    -g $RG `
    -n $DI `
    --query "{Name:name,Endpoint:properties.endpoint}" -o table
```

## Final resource structure

```
rg-student-ai-korea
│
├── ocrblobstorage
│   ├── prev
│   ├── to-verify
│   └── results
│
├── ocr-doc-processing-sb-2026
│   └── document-processing
│
└── ocr-doc-intel-2026
```

Note: The Azure Function itself remains local for now. We'll deploy it to an Azure Function App separately when you're ready.

Add this to `.gitignore`:

```
local.settings.json
.venv/
__pycache__/
```

Run commands sequentially. Keep all connection strings and keys private.
