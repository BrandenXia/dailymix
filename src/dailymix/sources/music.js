// Read-only: deliberately contains no setters or playlist operations.
function run() {
    const music = Application('Music');
    const tracks = music.libraryPlaylists[0].fileTracks;
    const fields = {
        id: tracks.persistentID(), title: tracks.name(), artist: tracks.artist(),
        album: tracks.album(), grouping: tracks.grouping(), plays: tracks.playedCount(),
        skips: tracks.skippedCount(), rating: tracks.rating(), added: tracks.dateAdded(),
        last_played: tracks.playedDate(), duration: tracks.duration(), enabled: tracks.enabled()
    };
    function date(value) {
        return value instanceof Date && !isNaN(value.getTime()) ? value.toISOString() : null;
    }
    const result = fields.id.map((id, i) => {
        const row = {};
        for (const key of Object.keys(fields)) row[key] = fields[key][i];
        row.added = date(row.added);
        row.last_played = date(row.last_played);
        return row;
    });
    return JSON.stringify({schema_version: 1, tracks: result});
}
