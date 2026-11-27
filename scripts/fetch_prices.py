"""Current AWS and Azure list prices for the processing cost estimate -> public/data/prices.json.

Like-for-like general-purpose VMs (64 vCPU, 256 GiB - enough RAM for Sentinel-1 TOPS coregistration),
Linux, Canada Central regions, USD:
  AWS    m7i.16xlarge, ca-central-1    on-demand: public price file behind aws.amazon.com/ec2/pricing
                                        spot:      public feed behind the Spot pricing page (current price)
         S3 Standard storage, first 50 TB tier
  Azure  Standard_D64s_v5, canadacentral  Azure Retail Prices API (on-demand, spot, 1-/3-year reservation)
         Blob Hot LRS storage, first tier

Re-run to refresh. If a source fails the previous value is kept and flagged.

Run:  python scripts/fetch_prices.py
"""
import datetime as dt
import json
import pathlib

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "public" / "data" / "prices.json"
S = requests.Session()
S.headers["User-Agent"] = "large-area-risk-canada/0.1 (research)"
AWS_TYPE, AZ_SKU, VCPU = "m7i.16xlarge", "Standard_D64s_v5", 64


def aws_on_demand():
    url = "https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/Canada%20(Central)/Linux/index.json"
    d = S.get(url, timeout=60).json()
    for v in d["regions"]["Canada (Central)"].values():
        if v.get("Instance Type") == AWS_TYPE:
            return float(v["price"])
    raise ValueError("instance not found")


def aws_spot():
    t = S.get("https://website.spot.ec2.aws.a2z.com/spot.js", timeout=60).text
    d = json.loads(t[t.index("(") + 1:t.rindex(")")])
    for r in d["config"]["regions"]:
        if r["region"] == "ca-central-1":
            for it in r["instanceTypes"]:
                for sz in it["sizes"]:
                    if sz["size"] == AWS_TYPE:
                        return float(next(c for c in sz["valueColumns"] if c["name"] == "linux")["prices"]["USD"])
    raise ValueError("spot price not found")


def aws_s3():
    d = S.get("https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/s3/USD/current/s3.json", timeout=60).json()
    return float(d["regions"]["Canada (Central)"]["Standard Storage First 50 TB per GB Mo"]["price"])


def azure(filter_):
    url = "https://prices.azure.com/api/retail/prices"
    return S.get(url, params={"$filter": filter_}, timeout=60).json()["Items"]


def azure_vm():
    items = azure(f"serviceName eq 'Virtual Machines' and armRegionName eq 'canadacentral' and armSkuName eq '{AZ_SKU}'")
    linux = [i for i in items if "Windows" not in i["productName"]]
    od = next(i["retailPrice"] for i in linux if i["type"] == "Consumption" and i["meterName"] == "D64s v5")
    spot = next(i["retailPrice"] for i in linux if i["type"] == "Consumption" and i["meterName"].endswith("Spot"))
    res = {i["reservationTerm"]: i["retailPrice"] for i in linux if i["type"] == "Reservation"}
    return od, spot, res.get("1 Year", 0) / 8760, res.get("3 Years", 0) / (3 * 8760)


def azure_blob():
    items = azure("serviceName eq 'Storage' and armRegionName eq 'canadacentral' and skuName eq 'Hot LRS' "
                  "and meterName eq 'Hot LRS Data Stored' and productName eq 'General Block Blob v2'")
    return min((i for i in items if i["tierMinimumUnits"] == 0), key=lambda i: i["retailPrice"])["retailPrice"]


def main():
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    stale = []

    def get(name, fn, fallback):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            stale.append(name)
            print(f"  {name}: {e} - keeping previous value")
            return fallback

    o_aws = (old.get("providers") or [{}])[0] if old else {}
    o_az = (old.get("providers") or [{}, {}])[1] if old else {}
    az = get("azure_vm", azure_vm, (o_az.get("on_demand_hr"), o_az.get("spot_hr"), o_az.get("reserved_1y_hr"), o_az.get("reserved_3y_hr")))
    out = {
        "fetched": dt.date.today().isoformat(), "currency": "USD", "vcpu_per_vm": VCPU, "stale": stale,
        "note": "List prices, Linux, Canada Central. Spot is the current market price and changes hourly.",
        "providers": [
            {"id": "aws", "name": "AWS", "region": "ca-central-1 (Canada Central)", "vm": AWS_TYPE, "vm_spec": "64 vCPU, 256 GiB",
             "on_demand_hr": get("aws_on_demand", aws_on_demand, o_aws.get("on_demand_hr")),
             "spot_hr": get("aws_spot", aws_spot, o_aws.get("spot_hr")),
             "storage": "S3 Standard", "storage_gb_month": get("aws_s3", aws_s3, o_aws.get("storage_gb_month"))},
            {"id": "azure", "name": "Azure", "region": "Canada Central", "vm": AZ_SKU.replace("Standard_", ""), "vm_spec": "64 vCPU, 256 GiB",
             "on_demand_hr": az[0], "spot_hr": az[1], "reserved_1y_hr": round(az[2], 4) if az[2] else None,
             "reserved_3y_hr": round(az[3], 4) if az[3] else None,
             "storage": "Blob Hot LRS", "storage_gb_month": get("azure_blob", azure_blob, o_az.get("storage_gb_month"))},
        ],
    }
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    for p in out["providers"]:
        print(f"{p['name']:6s} {p['vm']:14s} on-demand ${p['on_demand_hr']:.3f}/h (${p['on_demand_hr'] / VCPU:.4f}/vCPU-h)  "
              f"spot ${p['spot_hr']:.3f}/h  storage ${p['storage_gb_month']}/GB-mo")


if __name__ == "__main__":
    main()
