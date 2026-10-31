"""Private, durable image snapshots with explicit authorization-point revalidation."""

import json
import time

from common.images import capture_images,image_digest
from common.policy import issue_revision


class Images:
    def __init__(self,database,download,*,clock=time.time):
        self.database,self.download,self.clock=database,download,clock

    def snapshot(self,issue,*,revalidate=False):
        revision=issue_revision(issue)
        with self.database.transaction() as db:
            row=db.execute('SELECT digest,images FROM image_snapshots WHERE revision=?',(revision,)).fetchone()
        if row and not revalidate:
            images=json.loads(row[1])
            if image_digest(images)!=row[0]:
                raise ValueError('Stored image snapshot failed integrity verification')
            return images
        images=capture_images(issue.get('description'),self.download)
        with self.database.transaction() as db:
            db.execute('INSERT INTO image_snapshots VALUES (?,?,?,?) ON CONFLICT(revision) DO UPDATE SET '
                       'digest=excluded.digest,images=excluded.images,observed_at=excluded.observed_at',
                       (revision,image_digest(images),json.dumps(images),self.clock()))
        return images

    def prune(self,revisions):
        with self.database.transaction() as db:
            for row in db.execute('SELECT revision FROM image_snapshots').fetchall():
                if row[0] not in revisions:
                    db.execute('DELETE FROM image_snapshots WHERE revision=?',(row[0],))
