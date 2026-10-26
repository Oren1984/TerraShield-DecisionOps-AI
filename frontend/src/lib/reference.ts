import { useEffect, useState } from "react";
import districtJson from "../../../scenarios/district.json";
import { api, type Catalog, type District } from "./api";

// District geometry ships with the bundle (same file the simulator reads), so maps
// render without any network map tiles or a signed-in session.
export const DISTRICT = districtJson as unknown as District;

export function useDistrict(): District {
  return DISTRICT;
}

let catalogPromise: Promise<Catalog> | null = null;

export function useCatalog(): Catalog | null {
  const [c, setC] = useState<Catalog | null>(null);
  useEffect(() => {
    catalogPromise ??= api.catalog();
    catalogPromise.then(setC).catch(() => {
      catalogPromise = null;
    });
  }, []);
  return c;
}

export function assetName(id: string): string {
  return DISTRICT.assets.find((a) => a.id === id)?.name ?? id;
}

export function edgeName(id: string): string {
  return DISTRICT.road_edges.find((e) => e.id === id)?.name ?? id;
}
