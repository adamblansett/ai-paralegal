import os
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import BaseModel

_SAMPLES_DIR = Path(__file__).resolve().parents[4] / "samples"


class DMSNotConfiguredError(RuntimeError):
    """Raised when an enterprise DMS connector is missing credentials/configuration."""


class DMSFileItem(BaseModel):
    external_id: str
    name: str
    content_type: str
    document_type: str = "general"
    size_bytes: int
    modified_at: str
    source_uri: str


class DMSProviderInfo(BaseModel):
    provider: str
    display_name: str
    configured: bool
    description: str


class DMSConnector(ABC):
    """Abstract Document Management System (DMS) connector interface."""

    provider_name: str

    @abstractmethod
    async def list_files(self, folder_id: str | None = None) -> list[DMSFileItem]:
        """List importable legal files from the remote DMS."""

    @abstractmethod
    async def download_file(self, external_id: str) -> tuple[str, str, bytes]:
        """Return `(filename, content_type, raw_bytes)` for a remote DMS file."""


class LocalMockDMSConnector(DMSConnector):
    """Built-in offline DMS connector exposing local legal fixture repositories."""

    provider_name = "mock_dms"

    _BUILTIN_FIXTURE = (
        b"MASTER SUBSCRIPTION AGREEMENT\n"
        b"SECTION 7. LIMITATION OF LIABILITY.\n"
        b"Vendor aggregate liability shall not exceed $20,000 (one month fees).\n"
    )

    async def list_files(self, folder_id: str | None = None) -> list[DMSFileItem]:
        _ = folder_id
        items: list[DMSFileItem] = []
        if _SAMPLES_DIR.exists():
            for path in sorted(_SAMPLES_DIR.iterdir()):
                if not path.is_file():
                    continue
                ext = path.suffix.lower()
                doc_type = "email" if ext == ".eml" else "contract"
                ctype = "message/rfc822" if ext == ".eml" else "text/plain"
                stat = path.stat()
                items.append(
                    DMSFileItem(
                        external_id=path.name,
                        name=path.name,
                        content_type=ctype,
                        document_type=doc_type,
                        size_bytes=stat.st_size,
                        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                        source_uri=f"dms://mock_dms/samples/{path.name}",
                    )
                )
        if not items:
            items.append(
                DMSFileItem(
                    external_id="builtin_msa.txt",
                    name="builtin_msa.txt",
                    content_type="text/plain",
                    document_type="contract",
                    size_bytes=len(self._BUILTIN_FIXTURE),
                    modified_at=datetime.now(UTC).isoformat(),
                    source_uri="dms://mock_dms/builtin_msa.txt",
                )
            )
        return items

    async def download_file(self, external_id: str) -> tuple[str, str, bytes]:
        safe_name = Path(external_id).name
        if safe_name == "builtin_msa.txt":
            return safe_name, "text/plain", self._BUILTIN_FIXTURE
        target = (_SAMPLES_DIR / safe_name).resolve()
        if (
            not _SAMPLES_DIR.exists()
            or target.parent != _SAMPLES_DIR.resolve()
            or not target.exists()
        ):
            raise FileNotFoundError(f"DMS file '{external_id}' not found.")
        ctype = "message/rfc822" if target.suffix.lower() == ".eml" else "text/plain"
        return target.name, ctype, target.read_bytes()


class SharePointConnector(DMSConnector):
    """Microsoft Graph / SharePoint document library connector."""

    provider_name = "sharepoint"

    def __init__(self, access_token: str | None = None, drive_id: str | None = None) -> None:
        self.access_token = access_token or os.getenv("SHAREPOINT_ACCESS_TOKEN", "")
        self.drive_id = drive_id or os.getenv("SHAREPOINT_DRIVE_ID", "")

    def _require_config(self) -> None:
        if not self.access_token or not self.drive_id:
            raise DMSNotConfiguredError(
                "SharePoint connector requires SHAREPOINT_ACCESS_TOKEN and SHAREPOINT_DRIVE_ID."
            )

    async def list_files(self, folder_id: str | None = None) -> list[DMSFileItem]:
        self._require_config()
        item_path = f"items/{folder_id}/children" if folder_id else "root/children"
        url = f"https://graph.microsoft.com/v1.0/drives/{self.drive_id}/{item_path}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {self.access_token}"})
            resp.raise_for_status()
            entries = resp.json().get("value", [])
        out: list[DMSFileItem] = []
        for entry in entries:
            if "file" not in entry:
                continue
            out.append(
                DMSFileItem(
                    external_id=str(entry["id"]),
                    name=str(entry["name"]),
                    content_type=str(entry["file"].get("mimeType", "application/octet-stream")),
                    size_bytes=int(entry.get("size", 0)),
                    modified_at=str(entry.get("lastModifiedDateTime", "")),
                    source_uri=str(entry.get("webUrl", "")),
                )
            )
        return out

    async def download_file(self, external_id: str) -> tuple[str, str, bytes]:
        self._require_config()
        meta_url = f"https://graph.microsoft.com/v1.0/drives/{self.drive_id}/items/{external_id}"
        content_url = f"{meta_url}/content"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            meta_resp = await client.get(meta_url, headers=headers)
            meta_resp.raise_for_status()
            meta = meta_resp.json()
            dl_resp = await client.get(content_url, headers=headers)
            dl_resp.raise_for_status()
        name = str(meta.get("name") or f"{external_id}.bin")
        ctype = str(meta.get("file", {}).get("mimeType") or "application/octet-stream")
        return name, ctype, dl_resp.content


class GoogleDriveConnector(DMSConnector):
    """Google Drive REST v3 legal workspace connector."""

    provider_name = "google_drive"

    def __init__(self, access_token: str | None = None) -> None:
        self.access_token = access_token or os.getenv("GOOGLE_DRIVE_ACCESS_TOKEN", "")

    def _require_config(self) -> None:
        if not self.access_token:
            raise DMSNotConfiguredError(
                "Google Drive connector requires GOOGLE_DRIVE_ACCESS_TOKEN."
            )

    async def list_files(self, folder_id: str | None = None) -> list[DMSFileItem]:
        self._require_config()
        query = f"'{folder_id}' in parents and trashed = false" if folder_id else "trashed = false"
        url = "https://www.googleapis.com/drive/v3/files"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                url,
                params={
                    "q": query,
                    "pageSize": 25,
                    "fields": "files(id,name,mimeType,size,modifiedTime,webViewLink)",
                },
                headers={"Authorization": f"Bearer {self.access_token}"},
            )
            resp.raise_for_status()
            files = resp.json().get("files", [])
        return [
            DMSFileItem(
                external_id=str(f["id"]),
                name=str(f["name"]),
                content_type=str(f.get("mimeType", "application/octet-stream")),
                size_bytes=int(f.get("size", 0) or 0),
                modified_at=str(f.get("modifiedTime", "")),
                source_uri=str(f.get("webViewLink", "")),
            )
            for f in files
        ]

    async def download_file(self, external_id: str) -> tuple[str, str, bytes]:
        self._require_config()
        headers = {"Authorization": f"Bearer {self.access_token}"}
        async with httpx.AsyncClient(timeout=20.0) as client:
            meta_resp = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{external_id}",
                params={"fields": "name,mimeType"},
                headers=headers,
            )
            meta_resp.raise_for_status()
            meta = meta_resp.json()
            dl_resp = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{external_id}",
                params={"alt": "media"},
                headers=headers,
            )
            dl_resp.raise_for_status()
        return (
            str(meta.get("name") or f"{external_id}.bin"),
            str(meta.get("mimeType") or "application/octet-stream"),
            dl_resp.content,
        )


class IManageConnector(DMSConnector):
    """iManage Work REST API connector scaffold."""

    provider_name = "imanage"

    def __init__(self) -> None:
        self.base_url = os.getenv("IMANAGE_BASE_URL", "")
        self.token = os.getenv("IMANAGE_API_TOKEN", "")
        self.library = os.getenv("IMANAGE_LIBRARY", "ACTIVE")

    async def list_files(self, folder_id: str | None = None) -> list[DMSFileItem]:
        _ = folder_id
        if not self.base_url or not self.token:
            raise DMSNotConfiguredError(
                "iManage Work requires IMANAGE_BASE_URL and IMANAGE_API_TOKEN."
            )
        url = f"{self.base_url.rstrip('/')}/api/v2/customers/me/libraries/{self.library}/documents"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers={"X-Auth-Token": self.token})
            resp.raise_for_status()
            rows = resp.json().get("data", [])
        return [
            DMSFileItem(
                external_id=str(r.get("id")),
                name=f"{r.get('name', 'document')}.{r.get('extension', 'pdf')}",
                content_type="application/pdf",
                size_bytes=int(r.get("size", 0) or 0),
                modified_at=str(r.get("edit_date", "")),
                source_uri=f"imanage://{self.library}/{r.get('id')}",
            )
            for r in rows
        ]

    async def download_file(self, external_id: str) -> tuple[str, str, bytes]:
        if not self.base_url or not self.token:
            raise DMSNotConfiguredError(
                "iManage Work requires IMANAGE_BASE_URL and IMANAGE_API_TOKEN."
            )
        url = (
            f"{self.base_url.rstrip('/')}/api/v2/customers/me/"
            f"libraries/{self.library}/documents/{external_id}/download"
        )
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers={"X-Auth-Token": self.token})
            resp.raise_for_status()
        return f"{external_id}.pdf", "application/pdf", resp.content


class NetDocumentsConnector(DMSConnector):
    """NetDocuments REST API connector scaffold."""

    provider_name = "netdocuments"

    def __init__(self) -> None:
        self.base_url = os.getenv("NETDOCUMENTS_BASE_URL", "https://api.vault.netvoyage.com")
        self.token = os.getenv("NETDOCUMENTS_API_TOKEN", "")
        self.cabinet = os.getenv("NETDOCUMENTS_CABINET_ID", "")

    async def list_files(self, folder_id: str | None = None) -> list[DMSFileItem]:
        _ = folder_id
        if not self.token or not self.cabinet:
            raise DMSNotConfiguredError(
                "NetDocuments requires NETDOCUMENTS_API_TOKEN and NETDOCUMENTS_CABINET_ID."
            )
        url = f"{self.base_url.rstrip('/')}/v1/Search/{self.cabinet}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {self.token}"})
            resp.raise_for_status()
            rows = resp.json().get("standardList", [])
        return [
            DMSFileItem(
                external_id=str(r.get("id")),
                name=f"{r.get('name', 'doc')}.{r.get('ext', 'pdf')}",
                content_type="application/pdf",
                size_bytes=int(r.get("size", 0) or 0),
                modified_at=str(r.get("modified", "")),
                source_uri=f"nd://{self.cabinet}/{r.get('id')}",
            )
            for r in rows
        ]

    async def download_file(self, external_id: str) -> tuple[str, str, bytes]:
        if not self.token:
            raise DMSNotConfiguredError("NetDocuments requires NETDOCUMENTS_API_TOKEN.")
        url = f"{self.base_url.rstrip('/')}/v1/Document/{external_id}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {self.token}"})
            resp.raise_for_status()
        return f"{external_id}.pdf", "application/pdf", resp.content


def list_dms_providers() -> list[DMSProviderInfo]:
    """Return status and configuration state for all supported legal DMS connectors."""
    return [
        DMSProviderInfo(
            provider="mock_dms",
            display_name="Built-In Legal Fixture Vault",
            configured=True,
            description=(
                "Offline legal document repository serving sample contracts & discovery emails."
            ),
        ),
        DMSProviderInfo(
            provider="sharepoint",
            display_name="Microsoft SharePoint / M365",
            configured=bool(
                os.getenv("SHAREPOINT_ACCESS_TOKEN") and os.getenv("SHAREPOINT_DRIVE_ID")
            ),
            description=(
                "Import contracts and discovery folders from Microsoft Graph SharePoint drives."
            ),
        ),
        DMSProviderInfo(
            provider="google_drive",
            display_name="Google Workspace Drive",
            configured=bool(os.getenv("GOOGLE_DRIVE_ACCESS_TOKEN")),
            description="Import deal rooms and shared legal drives via Google Drive REST v3.",
        ),
        DMSProviderInfo(
            provider="imanage",
            display_name="iManage Work",
            configured=bool(os.getenv("IMANAGE_BASE_URL") and os.getenv("IMANAGE_API_TOKEN")),
            description=(
                "Connect directly to iManage Work matter workspaces and document libraries."
            ),
        ),
        DMSProviderInfo(
            provider="netdocuments",
            display_name="NetDocuments",
            configured=bool(
                os.getenv("NETDOCUMENTS_API_TOKEN") and os.getenv("NETDOCUMENTS_CABINET_ID")
            ),
            description="Import cloud legal cabinets and email stores from NetDocuments.",
        ),
    ]


def get_dms_connector(provider: str) -> DMSConnector:
    key = provider.strip().lower()
    mapping: dict[str, DMSConnector] = {
        "mock_dms": LocalMockDMSConnector(),
        "sharepoint": SharePointConnector(),
        "google_drive": GoogleDriveConnector(),
        "imanage": IManageConnector(),
        "netdocuments": NetDocumentsConnector(),
    }
    if key not in mapping:
        raise ValueError(f"Unsupported DMS provider: {provider}")
    return mapping[key]
