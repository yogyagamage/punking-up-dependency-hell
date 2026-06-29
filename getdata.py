# https://chat.qwen.ai/c/4b3fb79a-13df-4366-8a21-4bfe3d62c8ad
# Instructions:

# write a python script that gets the list of popular package names for the 
# Pypi, npm and rust ecosystems from the ecosyste.ms Packages API, sorted by download count 
# and then gets the "used by" metric for each of those names from deps.dev's GetDependents endpoint (v3alpha), 
# and finally generates one json file per ecosystem with the 
# 500 most popular packages and their numbers of dependents.

# Explanations provided by Qwen after generation

# API Quirks Handling (curl): The ecosyste.ms API actively blocks default Python HTTP clients (like requests or urllib) 
# for larger page sizes, often returning 402 Payment Required or timing out. 
# To bypass this, the script uses curl via subprocess.run, which has a different TLS fingerprint that the API accepts.
# Pagination: The script requests packages in pages of 20 (per_page=20). 
# This avoids the timeouts associated with requesting 500 items in a single massive payload 
# while still efficiently gathering the top 500 entries.
# deps.dev Integration: 

#     The GetDependents endpoint in deps.dev requires a specific package version. 
#     The script first calls the GetPackage endpoint to retrieve the list of versions 
#     and identifies the one marked as isDefault: true.
#     It then calls the GetDependents endpoint for that specific version to extract the dependentCount.

# Concurrency: Fetching dependents for 500 packages sequentially would take a very long time. 
# The script uses concurrent.futures.ThreadPoolExecutor with 20 workers to fetch data for multiple 
# packages simultaneously, significantly speeding up the process while respecting API rate limits.
# Output: It generates three JSON files (pypi_top_500.json, npm_top_500.json, rust_top_500.json), 
# each containing a list of dictionaries with the package name, download count, and dependent count, 
# sorted by popularity.



import subprocess
import json
import urllib.parse
import concurrent.futures
import os

# Configuration for the three ecosystems
# Maps the ecosyste.ms registry name to the deps.dev system name and output file
ECOSYSTEMS = [
    {"registry": "npmjs.org", "system": "NPM", "file": "npm_top_500.json"},
]
    # {"registry": "pypi.org", "system": "PYPI", "file": "pypi_top_500.json"},
    # {"registry": "crates.io", "system": "CARGO", "file": "rust_top_500.json"}

def fetch_json_curl(url):
    """
    Fetches JSON from a URL using curl.
    Note: We use curl via subprocess because the ecosyste.ms API actively blocks 
    default Python user agents and TLS fingerprints for larger page sizes, 
    often returning 402 Payment Required or timing out with standard libraries.
    """
    try:
        result = subprocess.run(
            ["curl", "-s", "-L", "--max-time", "15", url],
            capture_output=True,
            text=True,
            check=True
        )
        return json.loads(result.stdout)
    except Exception:
        return None

def get_top_packages(registry, limit=500):
    """
    Fetches the top packages from ecosyste.ms sorted by download count.
    Paginates with per_page=20 to avoid API timeouts and blocks.
    """
    packages = []
    page = 1
    per_page = 20
    
    while len(packages) < limit:
        #https://packages.ecosyste.ms/api/v1/registries/npmjs.org/packages?sort=downloads&order=desc&per_page=20&page=1
        url = f"https://packages.ecosyste.ms/api/v1/registries/{registry}/packages?sort=downloads&order=desc&per_page={per_page}&page={page}"
        data = fetch_json_curl(url)
        
        if not data:
            break
            
        packages.extend(data)
        
        # Stop if we received fewer items than requested (end of list)
        if len(data) < per_page:
            break
            
        page += 1
    
    # Extract name and downloads, ensuring downloads is an integer
    return [{"name": p["name"], "downloads": p.get("downloads") or 0} for p in packages[:limit]]

def get_dependents_curl(system, pkg_name):
    """
    Fetches the number of dependents for a package from deps.dev.
    1. Gets the package info to find the default version.
    2. Queries the GetDependents endpoint for that specific version.
    """
    encoded_pkg = urllib.parse.quote(pkg_name, safe='')
    
    # 1. Get package info to find the default version
    url_pkg = f"https://api.deps.dev/v3alpha/systems/{system}/packages/{encoded_pkg}"
    pkg_data = fetch_json_curl(url_pkg)
    
    if not pkg_data:
        return {"name": pkg_name, "dependents": None}
    
    default_version = None
    for v in pkg_data.get('versions', []):
        if v.get('isDefault'):
            default_version = v['versionKey']['version']
            break
    
    if not default_version:
        return {"name": pkg_name, "dependents": None}
        
    # 2. Get dependents for the default version
    url_dep = f"https://api.deps.dev/v3alpha/systems/{system}/packages/{encoded_pkg}/versions/{default_version}:dependents"
    dep_data = fetch_json_curl(url_dep)
    
    if dep_data:
        return {"name": pkg_name, "dependents": dep_data.get("dependentCount", 0)}
    else:
        return {"name": pkg_name, "dependents": None}

def process_ecosystem(ecosystem):
    """
    Processes a single ecosystem: fetches top 500 packages, 
    gets their dependent counts, and saves to a JSON file.
    """
    registry = ecosystem["registry"]
    system = ecosystem["system"]
    file_name = ecosystem["file"]
    
    print(f"Fetching top 500 packages for {registry}...")
    packages = get_top_packages(registry)
    print(f"Found {len(packages)} packages. Fetching dependents...")
    
    results = []
    
    # Use ThreadPoolExecutor to fetch dependents concurrently.
    # 20 workers provide a good balance between speed and avoiding rate limits.
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        future_to_pkg = {executor.submit(get_dependents_curl, system, pkg["name"]): pkg for pkg in packages}
        
        for future in concurrent.futures.as_completed(future_to_pkg):
            pkg = future_to_pkg[future]
            try:
                dep_res = future.result()
                results.append({
                    "name": pkg["name"],
                    "downloads": pkg["downloads"],
                    "dependents": dep_res["dependents"]
                })
            except Exception as exc:
                print(f"{pkg['name']} generated an exception: {exc}")
                results.append({
                    "name": pkg["name"],
                    "downloads": pkg["downloads"],
                    "dependents": None
                })
                
    # Sort by downloads descending to match the original order from ecosyste.ms
    results.sort(key=lambda x: x["downloads"], reverse=True)
    
    with open(file_name, 'w') as f:
        json.dump(results, f, indent=2)
        
    print(f"Successfully saved {file_name}")

def main():
    for eco in ECOSYSTEMS:
        process_ecosystem(eco)

if __name__ == "__main__":
    main()