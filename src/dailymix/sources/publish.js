// Only user-playlist membership is written. Library tracks are never deleted.
function run(argv) {
    ObjC.import('Foundation');
    const raw = $.NSString.stringWithContentsOfFileEncodingError(argv[0], $.NSUTF8StringEncoding, null);
    const request = JSON.parse(ObjC.unwrap(raw));
    return JSON.stringify(publish(Application('Music'), request));
}

function publish(music, request) {
    const marker = 'dailymix:managed:v1';
    const ids = request.ids;
    if (!ids.length || new Set(ids).size !== ids.length) throw Error('Expected nonempty unique track IDs');
    const library = music.libraryPlaylists[0].fileTracks;
    const libraryIds = library.persistentID();
    const references = {};
    libraryIds.forEach((id, i) => { references[id] = library[i]; });
    ids.forEach(id => { if (!references[id]) throw Error('Selected track no longer in library: ' + id); });
    const matches = music.userPlaylists.whose({name: request.name})();
    if (matches.length > 1) throw Error('Multiple playlists have the destination name');
    let target = matches.length ? matches[0] : null;
    if (target && (target.smart() || target.genius() || target.description() !== marker)) {
        throw Error('Destination is not a Daily Mix managed playlist; choose an unused name');
    }
    const previous = target ? target.tracks.persistentID() : [];
    previous.forEach(id => { if (!references[id]) throw Error('Destination includes a nonlocal track; refusing replacement'); });
    const targetId = target ? target.persistentID() : null;
    const same = JSON.stringify(previous) === JSON.stringify(ids);
    const plan = {name: request.name, playlist_id: targetId, previous_ids: previous,
                  ids: ids, action: same ? 'unchanged' : (target ? 'replace' : 'create')};
    if (request.dry_run) return plan;
    if (request.expected_playlist_id !== targetId || JSON.stringify(request.expected_previous_ids) !== JSON.stringify(previous)) {
        throw Error('Destination changed since preflight; retry publication');
    }
    if (same) return Object.assign(plan, {verified: true});
    function fill(playlist, trackIds) {
        trackIds.forEach(id => music.duplicate(references[id], {to: playlist}));
    }
    function verify(playlist, trackIds) {
        if (JSON.stringify(playlist.tracks.persistentID()) !== JSON.stringify(trackIds)) {
            throw Error('Playlist membership/order verification failed');
        }
    }
    function clear(playlist) {
        // Delete only references obtained from this user playlist, never library objects.
        const count = playlist.tracks.length;
        for (let i = count - 1; i >= 0; i--) music.delete(playlist.tracks[i]);
        if (playlist.tracks.length) throw Error('Playlist could not be cleared');
    }
    let stage = null;
    let touched = false;
    let created = false;
    try {
        stage = music.UserPlaylist({name: request.staging_name, description: marker});
        music.userPlaylists.push(stage);
        fill(stage, ids);
        verify(stage, ids);
        if (!target) {
            stage.name = request.name;
            target = stage;
            stage = null;
            created = true;
        } else {
            touched = true;
            clear(target);
            fill(target, ids);
        }
        verify(target, ids);
        return Object.assign(plan, {playlist_id: target.persistentID(), verified: true});
    } catch (error) {
        if (touched) {
            try { clear(target); fill(target, previous); verify(target, previous); }
            catch (rollback) { throw Error('Publication failed and rollback failed; restore from the saved backup: ' + rollback.message); }
        }
        if (created) music.delete(target);
        throw error;
    } finally {
        if (stage) music.delete(stage);
    }
}
