/* ── GitHub 링크 유틸: repo summary(url·기본 브랜치)로 폴더/파일 경로를
   tree/blob URL로 바꾼다. 코드분석 트리·드릴다운 모달이 공유해서 쓴다. */
import { fetchSummary } from './api.js';

const cache = new Map(); // repoId -> {base, branch}

export async function getRepoGithubInfo(repoId) {
  if (cache.has(repoId)) return cache.get(repoId);

  const summary = await fetchSummary(repoId).catch(() => null);
  const repo = (summary && summary.repository) || {};
  const m = (repo.url || '').match(/github\.com\/([^/]+\/[^/]+?)(?:\.git)?\/?$/);
  const info = {
    base: m ? `https://github.com/${m[1]}` : null,
    branch: repo.default_branch || 'main',
  };
  cache.set(repoId, info);
  return info;
}

const encodePath = (p) => p.split('/').map(encodeURIComponent).join('/');

export function githubFileUrl(info, filePath) {
  if (!info?.base || !filePath) return null;
  return `${info.base}/blob/${info.branch}/${encodePath(filePath)}`;
}

export function githubFolderUrl(info, folderPath) {
  if (!info?.base) return null;
  return folderPath ? `${info.base}/tree/${info.branch}/${encodePath(folderPath)}` : `${info.base}/tree/${info.branch}`;
}
