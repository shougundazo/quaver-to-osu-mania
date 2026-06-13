# quaver-to-osu-mania
Convert Quaver `.qp` mapsets to osu!mania `.osz`.

## Requirements

- Python 3
- PyYAML optional

The converter can run without PyYAML for common `.qua` files, but PyYAML is
recommended.

## Usage

```powershell
python qptoosu.py input.qp -o output.osz
```

If `-o` is omitted, the output file name is based on the map title.

## Options

Change the mapper name in the generated `.osu` files:

```powershell
python qptoosu.py input.qp -o output.osz --rename "MapperName"
```

Disable SV conversion:

```powershell
python qptoosu.py input.qp -o output.osz --mapoption nosv
```

Convert long notes to normal notes:

```powershell
python qptoosu.py input.qp -o output.osz --mapoption ln-to-tap
```

Convert all notes to long notes:

```powershell
python qptoosu.py input.qp -o output.osz --mapoption all-ln
```

Options can be combined:

```powershell
python qptoosu.py input.qp -o output.osz --rename "MapperName" --mapoption nosv,ln-to-tap
```

## Map Options

- `nosv`: skip Quaver `SliderVelocities`
- `ln-to-tap`: convert long notes to normal notes
- `all-ln`: convert every note to a long note

`ln-to-tap` and `all-ln` cannot be used together.

## Notes

Hitsounds, bookmarks, editor settings, storyboard data, and special SV
optimization are not handled.
