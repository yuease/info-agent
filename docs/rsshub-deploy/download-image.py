"""Download and verify an official GHCR image, then create a Docker load archive."""
import concurrent.futures, gzip, hashlib, io, json, pathlib, tarfile, urllib.request, time, sys
import argparse
parser=argparse.ArgumentParser()
parser.add_argument('--tag',default='latest',choices=['latest','chromium-bundled'])
tag=parser.parse_args().tag
sys.path.insert(0,str(pathlib.Path(__file__).parent/'local-deps'))

root = pathlib.Path(__file__).parent / 'image-cache'
root.mkdir(exist_ok=True)
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'})))
base = 'https://ghcr.io'
token = json.load(urllib.request.urlopen(base + '/token?scope=repository:diygod/rsshub:pull', timeout=30))['token']
headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json'}

def get(path):
    return urllib.request.urlopen(urllib.request.Request(base + '/v2/diygod/rsshub/' + path, headers=headers), timeout=120)

index = json.load(get('manifests/'+tag))
digest = next(m['digest'] for m in index['manifests'] if m.get('platform', {}).get('architecture') == 'amd64' and m['platform'].get('os') == 'linux')
raw = get('manifests/' + digest).read()
assert hashlib.sha256(raw).hexdigest() == digest.split(':')[1]
manifest = json.loads(raw)
(root / ('image-digest.txt' if tag=='latest' else 'chromium-digest.txt')).write_text(digest)

def blob(item):
    key = item['digest'].split(':')[1]
    path = root / key
    if not path.exists():
        def chunk(offset):
            end = min(offset + 512*1024, item['size']) - 1
            part_path = root / f'{key}.{offset}.part'
            if part_path.exists() and part_path.stat().st_size == end-offset+1:
                return part_path.read_bytes()
            request_headers = {**headers, 'Range': f'bytes={offset}-{end}'}
            for attempt in range(5):
                try:
                    req = urllib.request.Request(base + '/v2/diygod/rsshub/blobs/' + item['digest'], headers=request_headers)
                    with urllib.request.urlopen(req, timeout=60) as response:
                        if response.status == 206:
                            assert response.headers.get('Content-Range','').startswith(f'bytes {offset}-')
                            part = response.read()
                        else:
                            assert offset == 0
                            part = response.read()
                    assert len(part) == end-offset+1
                    part_path.write_bytes(part)
                    break
                except Exception:
                    if attempt == 4: raise
                    time.sleep(1)
            return part
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            parts=list(pool.map(chunk,range(0,item['size'],512*1024)))
        data = b''.join(parts)
        assert len(data) == item['size'] and hashlib.sha256(data).hexdigest() == key
        path.write_bytes(data)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == key
    print('verified', key[:12], path.stat().st_size, flush=True)
    return path

files = [blob(item) for item in [manifest['config']] + manifest['layers']]
archive = root / ('rsshub-light.tar.gz' if tag=='latest' else 'rsshub-chromium-bundled.tar.gz')
with tarfile.open(archive, 'w:gz',compresslevel=1) as tar:
    config_name = manifest['config']['digest'].split(':')[1] + '.json'
    tar.add(files[0], arcname=config_name)
    layers = []
    for i, path in enumerate(files[1:]):
        name = f'layer{i}/layer.tar'
        compressed = path.read_bytes()
        if compressed[:2] == b'\x1f\x8b':
            data = gzip.decompress(compressed)
        elif compressed[:4] == b'\x28\xb5\x2f\xfd':
            import zstandard
            with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(compressed)) as reader:
                data = reader.read()
        else:
            data = compressed
        info = tarfile.TarInfo(name); info.size = len(data)
        tar.addfile(info, io.BytesIO(data)); layers.append(name)
    data = json.dumps([{'Config': config_name, 'RepoTags': ['ghcr.io/diygod/rsshub:'+tag], 'Layers': layers}]).encode()
    info = tarfile.TarInfo('manifest.json'); info.size = len(data)
    tar.addfile(info, io.BytesIO(data))
print('archive', archive, archive.stat().st_size, 'digest', digest, flush=True)
