from git import Repo
import os
def collect_files(repo_path: str):
    
    repo = Repo(repo_path)
    
    files = []
    
    for item in repo.head.commit.tree.traverse():
        
        if item.type != "blob":
            continue
        
        path = item.path
        
        extension = os.path.splitext(path)[1]
        
        if extension.startswith("."):
            extension = extension[1:]
            
        files.append({
            "path": path,
            "extension": extension
        })
        
    return files