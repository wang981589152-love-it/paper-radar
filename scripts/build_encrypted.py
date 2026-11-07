#!/usr/bin/env python3
"""把 index.html + data/*.js 打包为 AES-256-GCM 加密的静态站点（输出 docs/，适配 GitHub Pages）

原理：
  - 访问密码 -- PBKDF2(SHA256, 200k 次) --> 256 位密钥
  - 主数据(meta.js 三件套)加密后内嵌进 HTML；年度分片(full_YYYY.js)加密为 docs/data/full_YYYY.bin
  - 浏览器端 WebCrypto 解锁后运行，服务器上只有密文，无密码看不到任何论文数据

用法：
  python3 scripts/build_encrypted.py --password 你的密码
  本地预览（加密版必须走 http，file:// 下年度分片会被浏览器拦截）：
    cd docs && python3 -m http.server 8000
"""
import argparse, base64, json, os, subprocess, sys
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

ROOT = Path(__file__).resolve().parent.parent
MAGIC = b"PRv1"

def derive_key(password: str, salt: bytes, iters: int) -> bytes:
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                      iterations=iters).derive(password.encode("utf-8"))

def seal(key: bytes, plain: bytes) -> bytes:
    """MAGIC(4B) + IV(12B) + ciphertext+tag"""
    iv = os.urandom(12)
    return MAGIC + iv + AESGCM(key).encrypt(iv, plain, None)

def js_globals(path: Path, expr: str, extra: str = ""):
    """用 node 安全求值 window.XXX，避免手写 JS 字面量解析"""
    code = ("const fs=require('fs');global.window={};"
            "eval(fs.readFileSync(process.argv[1],'utf8'));"
            "process.stdout.write(JSON.stringify(%s));" % expr)
    argv = ["node", "-e", code, str(path)] + ([extra] if extra else [])
    r = subprocess.run(argv, capture_output=True, text=True, check=True)
    return json.loads(r.stdout)

GATE_HTML = """
<style>
#gate{position:fixed;inset:0;z-index:999;display:none;justify-content:center;align-items:center;
  background:linear-gradient(135deg,#1b5e20 0%,#2e7d32 55%,#43a047 100%);font-family:inherit}
.gate-box{background:#fff;border-radius:16px;padding:34px 30px 26px;width:min(340px,88vw);
  box-shadow:0 12px 48px rgba(0,0,0,.3);text-align:center}
.gate-ico{font-size:42px}
.gate-ti{font-size:19px;font-weight:700;color:#1f2d24;margin-top:8px}
.gate-sub{font-size:13px;color:#5c6f63;margin:4px 0 16px}
#gate-pw{width:100%;box-sizing:border-box;border:1.5px solid #dfe7e0;border-radius:9px;
  padding:10px 13px;font-size:15px;outline:none;text-align:center}
#gate-pw:focus{border-color:#2e7d32}
#gate-btn{width:100%;margin-top:10px;background:#2e7d32;color:#fff;border:none;border-radius:9px;
  padding:10px 0;font-size:15px;font-weight:600;cursor:pointer;letter-spacing:6px}
#gate-btn:disabled{opacity:.6}
#gate-btn:hover{background:#1b5e20}
#gate-err{font-size:12.5px;color:#b71c1c;min-height:18px;margin-top:8px}
.gate-tip{font-size:11px;color:#9ab0a0;margin-top:6px}
</style>
<div id="gate" style="display:none">
  <div class="gate-box">
    <div class="gate-ico">🔐</div>
    <div class="gate-ti">论文雷达 · 加密版</div>
    <div class="gate-sub">数据已 AES-256-GCM 加密，请输入访问密码解锁</div>
    <input id="gate-pw" type="password" placeholder="访问密码" autocomplete="current-password">
    <button id="gate-btn">解锁</button>
    <div id="gate-err"></div>
    <div class="gate-tip">密码即密钥 · 仅在本机内存中解密，不发送到任何服务器</div>
  </div>
</div>
"""

RUNTIME_JS = """
/* ===== 加密站运行时：PBKDF2 + AES-GCM(WebCrypto) ===== */
(function(){
  var SALT="__SALT_B64__", ITERS=__ITERS__;
  var gate=document.getElementById("gate"),
      pin=document.getElementById("gate-pw"),
      gbtn=document.getElementById("gate-btn"),
      gerr=document.getElementById("gate-err");
  function b2buf(b64){var s=atob(b64),b=new Uint8Array(s.length);for(var i=0;i<s.length;i++)b[i]=s.charCodeAt(i);return b.buffer;}
  function derive(pw){
    var enc=new TextEncoder();
    return crypto.subtle.importKey("raw",enc.encode(pw),"PBKDF2",false,["deriveKey"])
      .then(function(k){return crypto.subtle.deriveKey(
        {name:"PBKDF2",salt:b2buf(SALT),iterations:ITERS,hash:"SHA-256"},k,
        {name:"AES-GCM",length:256},false,["decrypt"]);});
  }
  function dec(key,buf){
    var u8=new Uint8Array(buf);
    if(String.fromCharCode(u8[0],u8[1],u8[2],u8[3])!=="PRv1")
      return Promise.reject(new Error("format"));
    return crypto.subtle.decrypt({name:"AES-GCM",iv:u8.slice(4,16)},key,u8.slice(16));
  }
  /* 数据源：本站优先，超时/失败自动切换 jsdelivr 镜像（国内直连可达，手机无需 VPN） */
  var CDN_BASES=__CDN_BASES__;
  function fetchWithTimeout(url,ms){
    var ctl=("AbortController" in window)?new AbortController():null;
    var timer=ctl?setTimeout(function(){ctl.abort();},ms):null;
    return fetch(url,ctl?{signal:ctl.signal}:{})
      .then(function(r){if(timer)clearTimeout(timer);if(!r.ok)throw new Error(r.status);return r.arrayBuffer();})
      .catch(function(e){if(timer)clearTimeout(timer);throw e;});
  }
  function fetchBin(name){
    var list=[name];
    for(var k=0;k<CDN_BASES.length;k++)list.push(CDN_BASES[k]+"/"+name);
    var p=Promise.reject(new Error("start"));
    /* 本站只给 10 秒（可能被墙卡死，快速放弃）；CDN 源给 90 秒（10MB 大文件值得等） */
    list.forEach(function(u,idx){
      p=p.catch(function(){return fetchWithTimeout(u,idx===0?10000:90000);});
    });
    return p;
  }
  function doUnlock(pw){
    if(!pw){gerr.textContent="请输入密码";return;}
    gerr.textContent="解锁中…";gbtn.disabled=true;
    derive(pw).then(function(key){
      /* 分块拉取主数据并拼装 */
      var parts=[];
      for(var i=0;i<__META_PARTS__;i++)parts.push(i);
      return Promise.all(parts.map(function(i){
        return fetchBin("meta.part"+i+".bin");
      })).then(function(bufs){
        var total=0;bufs.forEach(function(b){total+=b.byteLength;});
        var all=new Uint8Array(total),off=0;
        bufs.forEach(function(b){all.set(new Uint8Array(b),off);off+=b.byteLength;});
        return dec(key,all.buffer);
      }).then(function(plain){
          var obj=JSON.parse(new TextDecoder().decode(plain));
          window.RADAR_UNITS=obj.units;window.RADAR_FIGS=obj.figs;window.RADAR_META=obj.meta;
          window.__KEY=key;
          gate.style.display="none";
          try{boot();}catch(e){gerr.textContent="数据初始化失败："+e.message;gbtn.disabled=false;}
        });
    }).catch(function(err){
      var netErr=err&&(err.name==="AbortError"||/Failed to fetch|NetworkError|load failed/i.test(err.message||""));
      gerr.textContent=netErr?"数据下载失败：网络不畅，请换网络后重试":"密码错误，请重试";
      gbtn.disabled=false;pin.select();
    });
  }
  /* 覆盖年度分片加载：改为拉取加密 .bin 并解密 */
  window.loadFull=function(year,cb){
    if(fullCache[year])return cb(fullCache[year]);
    if(fullLoading[year])return fullLoading[year].push(cb);
    fullLoading[year]=[cb];
    fetchBin("full_"+year+".bin")
      .then(function(buf){return dec(window.__KEY,buf);})
      .then(function(plain){
        fullCache[year]=JSON.parse(new TextDecoder().decode(plain));
        var cbs=fullLoading[year];delete fullLoading[year];
        cbs.forEach(function(f){f(fullCache[year]);});
      })
      .catch(function(){
        fullCache[year]={};
        var cbs=fullLoading[year];delete fullLoading[year];
        toast("加载摘要分片失败："+year);
        cbs.forEach(function(f){f({});});
      });
  };
  gbtn.onclick=function(){doUnlock(pin.value);};
  pin.addEventListener("keydown",function(e){if(e.key==="Enter")doUnlock(pin.value);});
  /* 支持 #pw=密码 自动解锁（仅自用方便，注意浏览器历史记录） */
  var m=(location.hash||"").match(/^#pw=(.+)$/);
  if(m){pin.value=decodeURIComponent(m[1]);doUnlock(pin.value);}
  else{gate.style.display="flex";setTimeout(function(){pin.focus();},50);}
})();
"""

def main():
    ap = argparse.ArgumentParser(description="构建加密版站点")
    ap.add_argument("--password", default="plant2026", help="访问密码（默认 plant2026）")
    ap.add_argument("--src", default=str(ROOT), help="项目根目录")
    ap.add_argument("--out", default=None, help="输出目录（默认 <src>/docs）")
    ap.add_argument("--iterations", type=int, default=200000, help="PBKDF2 迭代次数")
    ap.add_argument("--chunk-mb", type=int, default=10, help="meta 分块大小（MB）")
    ap.add_argument("--salt", default=None,
                    help="复用指定 salt（base64），使新 HTML 能解开旧构建加密的 .bin，避免重传数据")
    ap.add_argument("--src-html", default="",
                    help="明文版 HTML 源路径（默认自动：优先 src/index.html，其次 index.html）")
    ap.add_argument("--cdn",
                    default="https://cdn.jsdelivr.net/gh/wang981589152-love-it/paper-radar@main,"
                            "https://fastly.jsdelivr.net/gh/wang981589152-love-it/paper-radar@main,"
                            "https://gcore.jsdelivr.net/gh/wang981589152-love-it/paper-radar@main",
                    help="数据镜像 CDN 前缀（逗号分隔），本站下载失败/超时后依次回退")
    a = ap.parse_args()
    src, out = Path(a.src), Path(a.out or (Path(a.src) / "docs"))
    (out / "data").mkdir(parents=True, exist_ok=True)

    # 1) 主数据三件套 -> 一个 JSON -> 加密 -> 外置分块 meta.partN.bin（每块小于网页上传 25MB 限制）
    meta = js_globals(src / "data" / "meta.js",
                      "{units:window.RADAR_UNITS||{},figs:window.RADAR_FIGS||{},meta:window.RADAR_META}")
    meta_plain = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    if a.salt:
        salt = base64.b64decode(a.salt)
        assert len(salt) == 16, "--salt 必须是 16 字节的 base64"
    else:
        salt = os.urandom(16)
    key = derive_key(a.password, salt, a.iterations)
    meta_sealed = seal(key, meta_plain)
    chunk = a.chunk_mb * 1000 * 1000
    meta_parts = [meta_sealed[i:i + chunk] for i in range(0, len(meta_sealed), chunk)] or [b""]
    for idx, part in enumerate(meta_parts):
        (out / "data" / ("meta.part%d.bin" % idx)).write_bytes(part)

    # 2) 年度分片 -> docs/data/full_YYYY.bin
    years = sorted(p.stem.split("_", 1)[1] for p in (src / "data").glob("full_*.js"))
    for y in years:
        obj = js_globals(src / "data" / ("full_%s.js" % y),
                         "window.RADAR_FULL[process.argv[2]]", extra=y)
        plain = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        (out / "data" / ("full_%s.bin" % y)).write_bytes(seal(key, plain))

    # 3) 改写 HTML（不再内嵌数据，只注入解锁界面与运行时）
    # 模板源优先级：--src-html > src/index.html（明文模板，仓库部署用）> index.html（本地开发）
    if a.src_html:
        src_html = Path(a.src_html)
    elif (src / "src" / "index.html").exists():
        src_html = src / "src" / "index.html"
    else:
        src_html = src / "index.html"
    print(f"[模板] 使用明文源: {src_html}")
    html = src_html.read_text(encoding="utf-8")
    tag_meta = '<script src="data/meta.js"></script>'
    tag_boot = "<script>boot();</script>"
    if tag_meta not in html or tag_boot not in html:
        sys.exit("[!] index.html 缺少预期占位标记，请检查")
    runtime = RUNTIME_JS.replace("__SALT_B64__",
                                 base64.b64encode(salt).decode("ascii")
                                 ).replace("__ITERS__", str(a.iterations)
                                 ).replace("__META_PARTS__", str(len(meta_parts))
                                 ).replace("__CDN_BASES__", json.dumps(
                                     [u.rstrip("/") for u in a.cdn.split(",") if u.strip()]))
    gate_block = GATE_HTML + "<script>" + runtime + "</script>"
    html = html.replace(tag_meta, "")
    html = html.replace(tag_boot, gate_block)
    (out / "index.html").write_text(html, encoding="utf-8")

    sz_html = (out / "index.html").stat().st_size / 1e6
    sz_bins = sum((out / "data" / f).stat().st_size for f in os.listdir(out / "data")) / 1e6
    print(f"[加密] 完成 -> {out}")
    print(f"  index.html {sz_html:.1f} MB + data/ 加密数据 {sz_bins:.1f} MB（meta.bin + {len(years)} 个年度分片）")
    print(f"  年度分片: {', '.join(years)}")
    print(f"  提醒: 密码只保存在你手里；本地预览请 cd {out} && python3 -m http.server 8000")

if __name__ == "__main__":
    main()
