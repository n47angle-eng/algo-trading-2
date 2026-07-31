/** Generic YAML tree — no hardcoded strategy fields (constraint #10). */

export type YamlNode = null | boolean | number | string | YamlNode[] | {
  [key: string]: YamlNode;
};

export function YamlTree({ data, path = "root" }: { data: YamlNode; path?: string }) {
  if (data === null || data === undefined) {
    return <span className="yaml-tree__scalar">null</span>;
  }
  if (typeof data === "string" || typeof data === "number" || typeof data === "boolean") {
    return (
      <span className="yaml-tree__scalar table__mono">{String(data)}</span>
    );
  }
  if (Array.isArray(data)) {
    if (data.length === 0) {
      return <span className="yaml-tree__scalar">[]</span>;
    }
    return (
      <ul className="yaml-tree" aria-label={path}>
        {data.map((item, i) => (
          <li key={`${path}.${i}`}>
            <span className="yaml-tree__key">[{i}]</span>{" "}
            <YamlTree data={item} path={`${path}.${i}`} />
          </li>
        ))}
      </ul>
    );
  }
  const entries = Object.entries(data);
  if (entries.length === 0) {
    return <span className="yaml-tree__scalar">{"{}"}</span>;
  }
  return (
    <ul className="yaml-tree" aria-label={path}>
      {entries.map(([key, value]) => (
        <li key={`${path}.${key}`}>
          <span className="yaml-tree__key">{key}</span>
          {isPlain(value) ? (
            <>
              : <YamlTree data={value} path={`${path}.${key}`} />
            </>
          ) : (
            <YamlTree data={value} path={`${path}.${key}`} />
          )}
        </li>
      ))}
    </ul>
  );
}

function isPlain(value: YamlNode): boolean {
  return (
    value === null ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  );
}
