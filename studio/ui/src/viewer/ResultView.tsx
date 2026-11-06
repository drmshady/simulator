import { Stack } from '@mantine/core';
import { useCallback, useEffect, useMemo, useState } from 'react';

import type { AssetLink } from '../api/types';
import { displayRange, type FieldName } from './colourScale';
import { GlbModel } from './GlbModel';
import { useFieldValues } from './useFieldValues';
import { ContinuousLegend, FrostLegend } from './Legend';
import { Scene } from './Scene';

export interface ResultViewProps {
  assets: AssetLink[];
  field: FieldName;
  magnification?: number;
  hiddenParts?: ReadonlySet<string>;
  height?: number;
}

function Values({ url, field, onValues }: { url: string; field: FieldName; onValues: (url: string, v: Float32Array | null) => void }) {
  const values = useFieldValues(url, field);
  useEffect(() => onValues(url, values), [url, values, onValues]);
  return null;
}

/** Result surfaces of one load case on one shared colour range (Principle VII), with their legend. */
export function ResultView({ assets, field, magnification = 0, hiddenParts, height = 420 }: ResultViewProps) {
  const [values, setValues] = useState<Record<string, Float32Array | null>>({});
  const onValues = useCallback((url: string, v: Float32Array | null) => {
    setValues((current) => (current[url] === v ? current : { ...current, [url]: v }));
  }, []);
  const shown = assets.filter((a) => !hiddenParts?.has(a.part));
  const range = useMemo(
    () => displayRange(shown.map((a) => values[a.url]).filter((v): v is Float32Array => v != null && v.length > 0)),
    [shown, values],
  );
  return (
    <Stack gap="sm">
      <Scene height={height}>
        {shown.map((asset) => (
          <group key={asset.url}>
            <Values url={asset.url} field={field} onValues={onValues} />
            <GlbModel url={asset.url} field={field} range={range} magnification={magnification} />
          </group>
        ))}
      </Scene>
      {field === 'frost' ? <FrostLegend /> : <ContinuousLegend field={field} range={range} />}
    </Stack>
  );
}
