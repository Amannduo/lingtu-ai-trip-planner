"""Unsplash图片服务"""

from typing import List, Optional

import httpx

from ..config import get_settings


class UnsplashService:
    """Unsplash图片服务类（使用 httpx 非阻塞客户端）"""

    def __init__(self):
        settings = get_settings()
        self.access_key = settings.unsplash_access_key
        self.base_url = "https://api.unsplash.com"
        self._warned_missing_key = False
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=15)
        return self._client

    def search_photos(self, query: str, per_page: int = 5) -> List[dict]:
        if not self.access_key:
            if not self._warned_missing_key:
                print("Unsplash Access Key 未配置，跳过景点图片搜索。")
                self._warned_missing_key = True
            return []

        try:
            response = self.client.get(
                f"{self.base_url}/search/photos",
                params={
                    "query": query,
                    "per_page": per_page,
                    "client_id": self.access_key,
                },
            )
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])

            photos: List[dict] = []
            for photo in results:
                photos.append({
                    "id": photo.get("id"),
                    "url": photo.get("urls", {}).get("regular"),
                    "thumb": photo.get("urls", {}).get("thumb"),
                    "description": photo.get("description") or photo.get("alt_description"),
                    "photographer": photo.get("user", {}).get("name"),
                })
            return photos

        except Exception as e:
            print(f"Unsplash search failed: {type(e).__name__}: {e}")
            return []
    
    def get_photo_url(self, query: str) -> Optional[str]:
        """
        获取单张图片URL

        Args:
            query: 搜索关键词

        Returns:
            图片URL
        """
        photos = self.search_photos(query, per_page=1)
        if photos:
            return photos[0].get("url")
        return None


# 全局服务实例
_unsplash_service = None


def get_unsplash_service() -> UnsplashService:
    """获取Unsplash服务实例(单例模式)"""
    global _unsplash_service
    
    if _unsplash_service is None:
        _unsplash_service = UnsplashService()
    
    return _unsplash_service
