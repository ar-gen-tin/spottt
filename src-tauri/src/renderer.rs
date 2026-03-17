//! ASCII art renderer — 7 styles, dithering, color modes.
//! Produces HTML spans directly (no ANSI intermediate).

use image::RgbImage;

/// Available art styles
pub const STYLES: &[&str] = &[
    "braille",
    "block",
    "classic",
    "edge",
    "particles",
    "retro-art",
    "terminal",
];

// Character ramps
const DEFAULT_RAMP: &str =
    "$@B%8&WM#*oahkbdpqwmZO0QLCJUYXzcvunxrjft/\\|()1{}[]?-_+~<>i!lI;:,\"^`. ";
const RETRO_RAMP: &str = "\u{2588}\u{2593}\u{2592}\u{2591}#%=+:. ";
const BLOCK_CHARS: &str = "\u{2588}\u{2593}\u{2592}\u{2591} ";
const PARTICLE_CHARS: &[char] = &['\u{2022}', '\u{00B7}', '\u{2027}', '*', '\u{2219}', '+'];

// Braille dot map: [row][col] → bit
const BRAILLE_DOT_MAP: [[u32; 2]; 4] = [
    [0x01, 0x08],
    [0x02, 0x10],
    [0x04, 0x20],
    [0x40, 0x80],
];

const CHAR_ASPECT: f64 = 2.0;

/// Preset config
struct Preset {
    ramp: &'static str,
    color_mode: ColorMode,
    dither: DitherAlgo,
    dither_strength: f64,
}

#[derive(Clone, Copy)]
enum ColorMode {
    Original,
    Matrix,
    Amber,
}

#[derive(Clone, Copy)]
enum DitherAlgo {
    None,
    Atkinson,
}

fn get_preset(style: &str) -> Option<Preset> {
    match style {
        "retro-art" => Some(Preset {
            ramp: RETRO_RAMP,
            color_mode: ColorMode::Amber,
            dither: DitherAlgo::Atkinson,
            dither_strength: 0.9,
        }),
        "terminal" => Some(Preset {
            ramp: DEFAULT_RAMP,
            color_mode: ColorMode::Matrix,
            dither: DitherAlgo::None,
            dither_strength: 0.8,
        }),
        _ => None,
    }
}

pub struct AsciiRenderer {
    pub style_index: usize,
    cache: std::collections::HashMap<(String, String, usize), String>, // (track_id, style, cols) -> html
}

impl AsciiRenderer {
    pub fn new() -> Self {
        Self {
            style_index: 0,
            cache: std::collections::HashMap::new(),
        }
    }

    pub fn current_style(&self) -> &str {
        STYLES[self.style_index % STYLES.len()]
    }

    pub fn next_style(&mut self) {
        self.style_index = (self.style_index + 1) % STYLES.len();
    }

    pub fn prev_style(&mut self) {
        self.style_index = (self.style_index + STYLES.len() - 1) % STYLES.len();
    }

    pub fn clear_cache(&mut self) {
        self.cache.clear();
    }

    /// Render image bytes to HTML string with colored spans.
    pub fn render_to_html(
        &mut self,
        image_bytes: &[u8],
        cols: usize,
        track_id: Option<&str>,
    ) -> Result<String, String> {
        let cache_key = (
            track_id.unwrap_or("").to_string(),
            self.current_style().to_string(),
            cols,
        );
        if let Some(cached) = self.cache.get(&cache_key) {
            return Ok(cached.clone());
        }

        let img = image::load_from_memory(image_bytes)
            .map_err(|e| format!("Image load failed: {}", e))?;
        let img = img.to_rgb8();

        let style = self.current_style().to_string();
        let html = render_style(&img, &style, cols);

        if track_id.is_some() {
            // Keep cache bounded
            if self.cache.len() > 20 {
                self.cache.clear();
            }
            self.cache.insert(cache_key, html.clone());
        }

        Ok(html)
    }
}

/// Core rendering: image → chars + colors → HTML
fn render_style(img: &RgbImage, style: &str, cols: usize) -> String {
    match style {
        "braille" => render_braille(img, cols),
        "edge" => render_edge(img, cols),
        "block" => render_block(img, cols),
        "particles" => render_particles(img, cols),
        "classic" => render_classic(img, cols, DEFAULT_RAMP, ColorMode::Original, DitherAlgo::None, 0.8),
        "retro-art" | "terminal" => {
            let preset = get_preset(style).unwrap();
            render_classic(img, cols, preset.ramp, preset.color_mode, preset.dither, preset.dither_strength)
        }
        _ => render_classic(img, cols, DEFAULT_RAMP, ColorMode::Original, DitherAlgo::None, 0.8),
    }
}

// ── Image processing helpers ──

struct TileGrid {
    brightness: Vec<Vec<f64>>,
    colors: Vec<Vec<[u8; 3]>>,
    rows: usize,
    cols: usize,
}

fn process_image(img: &RgbImage, cols: usize) -> TileGrid {
    let (img_w, img_h) = (img.width() as usize, img.height() as usize);
    let cols = cols.min(img_w).max(1);
    let tile_w = img_w as f64 / cols as f64;
    let tile_h = tile_w * CHAR_ASPECT;
    let rows = (img_h as f64 / tile_h).max(1.0) as usize;

    // Resize using simple area sampling
    let mut brightness = vec![vec![0.0f64; cols]; rows];
    let mut colors = vec![vec![[0u8; 3]; cols]; rows];

    for r in 0..rows {
        for c in 0..cols {
            let x = (c as f64 * img_w as f64 / cols as f64) as u32;
            let y = (r as f64 * img_h as f64 / rows as f64) as u32;
            let x = x.min(img.width() - 1);
            let y = y.min(img.height() - 1);
            let px = img.get_pixel(x, y).0;
            let lum = 0.299 * px[0] as f64 + 0.587 * px[1] as f64 + 0.114 * px[2] as f64;
            brightness[r][c] = lum;
            colors[r][c] = px;
        }
    }

    TileGrid {
        brightness,
        colors,
        rows,
        cols,
    }
}

// ── Dithering ──

fn atkinson_dither(brightness: &mut Vec<Vec<f64>>, levels: usize, strength: f64) {
    let rows = brightness.len();
    let cols = if rows > 0 { brightness[0].len() } else { 0 };
    let step = 255.0 / (levels.max(2) - 1) as f64;

    for y in 0..rows {
        for x in 0..cols {
            let old = brightness[y][x];
            let new = (old / step).round() * step;
            let new = new.clamp(0.0, 255.0);
            brightness[y][x] = new;
            // Atkinson: 1/8 error to each of 6 neighbors (6/8 total, scaled by strength)
            let error = (old - new) * strength / 8.0;

            if x + 1 < cols {
                brightness[y][x + 1] = (brightness[y][x + 1] + error).clamp(0.0, 255.0);
            }
            if x + 2 < cols {
                brightness[y][x + 2] = (brightness[y][x + 2] + error).clamp(0.0, 255.0);
            }
            if y + 1 < rows {
                if x > 0 {
                    brightness[y + 1][x - 1] =
                        (brightness[y + 1][x - 1] + error).clamp(0.0, 255.0);
                }
                brightness[y + 1][x] = (brightness[y + 1][x] + error).clamp(0.0, 255.0);
                if x + 1 < cols {
                    brightness[y + 1][x + 1] =
                        (brightness[y + 1][x + 1] + error).clamp(0.0, 255.0);
                }
            }
            if y + 2 < rows {
                brightness[y + 2][x] = (brightness[y + 2][x] + error).clamp(0.0, 255.0);
            }
        }
    }
}

// ── Color application ──

fn apply_color(brightness: f64, original: [u8; 3], mode: ColorMode) -> [u8; 3] {
    match mode {
        ColorMode::Original => original,
        ColorMode::Matrix => [0, brightness.clamp(0.0, 255.0) as u8, 0],
        ColorMode::Amber => [
            brightness.clamp(0.0, 255.0) as u8,
            (brightness * 0.6).clamp(0.0, 255.0) as u8,
            0,
        ],
    }
}

// ── HTML generation ──

fn chars_colors_to_html(
    chars: &[Vec<char>],
    colors: &[Vec<[u8; 3]>],
) -> String {
    let mut html = String::with_capacity(chars.len() * chars.get(0).map_or(0, |r| r.len()) * 40);

    for (r, row) in chars.iter().enumerate() {
        let mut prev_rgb: Option<[u8; 3]> = None;
        for (c, &ch) in row.iter().enumerate() {
            let rgb = colors
                .get(r)
                .and_then(|cr| cr.get(c))
                .copied()
                .unwrap_or([128, 128, 128]);

            if prev_rgb != Some(rgb) {
                if prev_rgb.is_some() {
                    html.push_str("</span>");
                }
                html.push_str(&format!(
                    "<span style=\"color:rgb({},{},{})\">",
                    rgb[0], rgb[1], rgb[2]
                ));
                prev_rgb = Some(rgb);
            }

            match ch {
                '<' => html.push_str("&lt;"),
                '>' => html.push_str("&gt;"),
                '&' => html.push_str("&amp;"),
                '"' => html.push_str("&quot;"),
                '\'' => html.push_str("&#39;"),
                _ => html.push(ch),
            }
        }
        if prev_rgb.is_some() {
            html.push_str("</span>");
        }
        if r < chars.len() - 1 {
            html.push_str("<br>");
        }
    }

    html
}

// ── Style implementations ──

fn render_classic(
    img: &RgbImage,
    cols: usize,
    ramp: &str,
    color_mode: ColorMode,
    dither: DitherAlgo,
    dither_strength: f64,
) -> String {
    let mut grid = process_image(img, cols);

    match dither {
        DitherAlgo::Atkinson => {
            atkinson_dither(&mut grid.brightness, ramp.chars().count(), dither_strength);
        }
        DitherAlgo::None => {}
    }

    let ramp_chars: Vec<char> = ramp.chars().collect();
    let ramp_len = ramp_chars.len();

    let mut chars = Vec::with_capacity(grid.rows);
    let mut colors = Vec::with_capacity(grid.rows);

    for r in 0..grid.rows {
        let mut char_row = Vec::with_capacity(grid.cols);
        let mut color_row = Vec::with_capacity(grid.cols);
        for c in 0..grid.cols {
            let b = grid.brightness[r][c];
            let idx = ((b / 255.0) * (ramp_len - 1) as f64)
                .round()
                .clamp(0.0, (ramp_len - 1) as f64) as usize;
            char_row.push(ramp_chars[idx]);
            color_row.push(apply_color(b, grid.colors[r][c], color_mode));
        }
        chars.push(char_row);
        colors.push(color_row);
    }

    chars_colors_to_html(&chars, &colors)
}

fn render_block(img: &RgbImage, cols: usize) -> String {
    let grid = process_image(img, cols);
    let blocks: Vec<char> = BLOCK_CHARS.chars().collect();
    let len = blocks.len();

    let mut chars = Vec::with_capacity(grid.rows);
    let mut colors = Vec::with_capacity(grid.rows);

    for r in 0..grid.rows {
        let mut char_row = Vec::with_capacity(grid.cols);
        let mut color_row = Vec::with_capacity(grid.cols);
        for c in 0..grid.cols {
            let b = grid.brightness[r][c];
            // Invert: dark → full block, light → space
            let idx = ((255.0 - b) / 255.0 * (len - 1) as f64)
                .round()
                .clamp(0.0, (len - 1) as f64) as usize;
            char_row.push(blocks[idx]);
            color_row.push(grid.colors[r][c]);
        }
        chars.push(char_row);
        colors.push(color_row);
    }

    chars_colors_to_html(&chars, &colors)
}

fn render_braille(img: &RgbImage, cols: usize) -> String {
    let (img_w, img_h) = (img.width() as usize, img.height() as usize);
    let cols = cols.min(img_w / 2).max(1);

    // Each braille char = 2x4 dots
    let dot_cols = cols * 2;
    let tile_w = img_w as f64 / dot_cols as f64;
    let tile_h = tile_w * (CHAR_ASPECT / 2.0);
    let dot_rows = ((img_h as f64 / tile_h) as usize / 4) * 4;
    let dot_rows = dot_rows.max(4);
    let char_rows = dot_rows / 4;

    // Sample high-res brightness
    let mut brightness_hi = vec![vec![0.0f64; dot_cols]; dot_rows];
    for r in 0..dot_rows {
        for c in 0..dot_cols {
            let x = (c as f64 * img_w as f64 / dot_cols as f64) as u32;
            let y = (r as f64 * img_h as f64 / dot_rows as f64) as u32;
            let px = img.get_pixel(x.min(img.width() - 1), y.min(img.height() - 1)).0;
            brightness_hi[r][c] = 0.299 * px[0] as f64 + 0.587 * px[1] as f64 + 0.114 * px[2] as f64;
        }
    }

    // Compute threshold
    let mut sum = 0.0;
    let mut count = 0;
    for row in &brightness_hi {
        for &v in row {
            sum += v;
            count += 1;
        }
    }
    let threshold = if count > 0 { sum / count as f64 } else { 128.0 };

    // Low-res colors
    let mut colors_lo = vec![vec![[128u8; 3]; cols]; char_rows];
    for r in 0..char_rows {
        for c in 0..cols {
            let x = (c as f64 * img_w as f64 / cols as f64) as u32;
            let y = (r as f64 * img_h as f64 / char_rows as f64) as u32;
            let px = img.get_pixel(x.min(img.width() - 1), y.min(img.height() - 1)).0;
            colors_lo[r][c] = px;
        }
    }

    // Build braille chars
    let mut chars = Vec::with_capacity(char_rows);
    for cr in 0..char_rows {
        let mut row = Vec::with_capacity(cols);
        for cc in 0..cols {
            let mut codepoint: u32 = 0x2800;
            for dr in 0..4 {
                for dc in 0..2 {
                    let py = cr * 4 + dr;
                    let px = cc * 2 + dc;
                    if py < dot_rows && px < dot_cols && brightness_hi[py][px] < threshold {
                        codepoint |= BRAILLE_DOT_MAP[dr][dc];
                    }
                }
            }
            row.push(char::from_u32(codepoint).unwrap_or(' '));
        }
        chars.push(row);
    }

    chars_colors_to_html(&chars, &colors_lo)
}

fn render_edge(img: &RgbImage, cols: usize) -> String {
    let grid = process_image(img, cols);
    let rows = grid.rows;
    let cols_n = grid.cols;

    // Sobel on brightness
    let b = &grid.brightness;
    let mut chars = Vec::with_capacity(rows);
    let mut colors = Vec::with_capacity(rows);

    for r in 0..rows {
        let mut char_row = Vec::with_capacity(cols_n);
        let mut color_row = Vec::with_capacity(cols_n);
        for c in 0..cols_n {
            // Sobel with edge clamping
            let get = |ry: i32, cx: i32| -> f64 {
                let ry = ry.clamp(0, rows as i32 - 1) as usize;
                let cx = cx.clamp(0, cols_n as i32 - 1) as usize;
                b[ry][cx]
            };

            let ri = r as i32;
            let ci = c as i32;

            let gx = -get(ri - 1, ci - 1) + get(ri - 1, ci + 1)
                - 2.0 * get(ri, ci - 1)
                + 2.0 * get(ri, ci + 1)
                - get(ri + 1, ci - 1)
                + get(ri + 1, ci + 1);

            let gy = -get(ri - 1, ci - 1) - 2.0 * get(ri - 1, ci) - get(ri - 1, ci + 1)
                + get(ri + 1, ci - 1)
                + 2.0 * get(ri + 1, ci)
                + get(ri + 1, ci + 1);

            let mag = (gx * gx + gy * gy).sqrt();

            let ch = if mag < 30.0 {
                ' '
            } else {
                let deg = gy.atan2(gx).to_degrees().rem_euclid(180.0);
                if deg < 22.5 || deg >= 157.5 {
                    '\u{2014}' // —
                } else if deg < 67.5 {
                    '/'
                } else if deg < 112.5 {
                    '|'
                } else {
                    '\\'
                }
            };

            char_row.push(ch);
            color_row.push(grid.colors[r][c]);
        }
        chars.push(char_row);
        colors.push(color_row);
    }

    chars_colors_to_html(&chars, &colors)
}

fn render_particles(img: &RgbImage, cols: usize) -> String {
    let grid = process_image(img, cols);

    // Deterministic pseudo-random using simple hash
    let mut chars = Vec::with_capacity(grid.rows);
    let mut colors = Vec::with_capacity(grid.rows);

    for r in 0..grid.rows {
        let mut char_row = Vec::with_capacity(grid.cols);
        let mut color_row = Vec::with_capacity(grid.cols);
        for c in 0..grid.cols {
            let b = grid.brightness[r][c];
            let prob = (255.0 - b) / 255.0 * 0.9;

            // Simple deterministic hash for reproducibility
            let hash = ((r * 7919 + c * 104729 + 42) % 1000) as f64 / 1000.0;

            if hash < prob {
                let dark = (255.0 - b) / 255.0;
                let idx = (dark * PARTICLE_CHARS.len() as f64) as usize;
                let idx = idx.min(PARTICLE_CHARS.len() - 1);
                char_row.push(PARTICLE_CHARS[idx]);
                color_row.push(grid.colors[r][c]);
            } else {
                char_row.push(' ');
                color_row.push([0, 0, 0]);
            }
        }
        chars.push(char_row);
        colors.push(color_row);
    }

    chars_colors_to_html(&chars, &colors)
}
