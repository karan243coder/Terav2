"""
TeraBox cookie (ndus) verify karne ka simple script.

Usage:
  1) Koi bhi TeraBox share link ready rakho
  2) python test_cookie.py "https://www.terabox.com/s/1AbCd..."
     (TERABOX_COOKIE env se cookie lena hota hai, ya -c flag se de sakte ho)

Cookie sahi hai to file list + dlink print hoga.
"""
import os
import sys
from dotenv import load_dotenv

from terabox import TeraBox, TeraBoxError

load_dotenv()


def main():
    url = None
    cookie = os.environ.get("TERABOX_COOKIE", "").strip()
    args = sys.argv[1:]
    if "-c" in args:
        i = args.index("-c")
        try:
            cookie = args[i + 1]
            del args[i:i + 2]
        except IndexError:
            print("-c ke baad cookie value chahiye")
            return 1
    for a in args:
        if a.startswith("http"):
            url = a
            break
    if not url:
        print('Usage: python test_cookie.py <terabox-share-url> [-c "ndus=VALUE"]')
        return 1

    tb = TeraBox(cookie=cookie)
    print(f"🔍 Resolving: {url}")
    print(f"🍪 Cookie: {'✅ set (' + str(len(cookie)) + ' chars)' if cookie else '❌ Nahi hai (guest mode)'}\n")
    try:
        files = tb.get_share_info(url)
        print(f"✅ File list OK — {len(files)} items:")
        for i, f in enumerate(files, 1):
            print(f"   [{i}] {'📁' if f.is_dir else '🎬'} {f.name}  ({f.size_str})  dlink={'yes' if f.dlink else 'no'}")
        non_dir = [f for f in files if not f.is_dir]
        if not non_dir:
            print("\n⚠️ Sirf folders hain, dlink test skip.")
            return 0
        print(f"\n🔑 dlink fetch karta hoon: {non_dir[0].name}")
        dlink = tb.get_dlink(url, non_dir[0].fs_id)
        print("✅✅ DLINK MILE (cookie kaam kar raha hai!):\n" + dlink[:200])
        return 0
    except TeraBoxError as e:
        print(f"❌ {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
