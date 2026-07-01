import sqlite3
import pandas as pd

conn = sqlite3.connect("data/pricing.db")

df = pd.read_sql_query("SELECT * FROM price_snapshots", conn)

df.to_csv("outputs/pricing_sample.csv", index=False)

print("Exported successfully")