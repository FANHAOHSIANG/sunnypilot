#!/usr/bin/env python3
"""Read-only batch summary of archived Hyundai FCA/SCC CAN traffic.

Requires openpilot runtime (LogReader); never sends CAN traffic or changes Params.
No payload contents included in output.
"""
import argparse
import json
from collections import Counter
from pathlib import Path
from openpilot.tools.lib.logreader import LogReader

IDS = {0x160:"ADRV_0x160",0x1a0:"SCC_CONTROL",0x730:"ADAS_UDS",0x7d0:"RADAR_UDS"}

def inspect_file(path):
  c=Counter()
  sample16=set()
  for event in LogReader(str(path)):
    direction=event.which()
    if direction not in ("can","sendcan"):
      continue
    for msg in getattr(event,direction):
      addr=int(msg.address)
      if addr in IDS:
        bus=int(msg.src)
        c[(direction,IDS[addr],bus)]+=1
        if addr==0x160 and direction=="sendcan":
          sample16.add(bytes(msg.dat)[-6:].hex())
  return {"segment_file":path.name,"counts": [
    {"direction":direction,"message":name,"bus":bus,"frames":n}
    for (direction,name,bus),n in sorted(c.items())
  ],"sendcan_160_unique_tail_count":len(sample16)}

def main():
  p=argparse.ArgumentParser()
  p.add_argument("files",nargs="+",type=Path)
  p.add_argument("--output",type=Path)
  args=p.parse_args()
  res={"warning":"Passively observed CAN traffic does not verify factory FCA/AEB operation.",
       "segments":[inspect_file(f) for f in args.files]}
  out=json.dumps(res,ensure_ascii=False,indent=2)
  if args.output:
    args.output.write_text(out+"\n",encoding="utf-8")
    print("Saved:",args.output)
  for s in res["segments"]:
    d={(x["direction"],x["message"],x["bus"]):x["frames"] for x in s["counts"]}
    print(s["segment_file"],"tx160",sum(n for (direction,name,bus),n in d.items() if direction=="sendcan" and name=="ADRV_0x160"),"tx1a0",sum(n for (direction,name,bus),n in d.items() if direction=="sendcan" and name=="SCC_CONTROL"))
if __name__=="__main__":
  main()
