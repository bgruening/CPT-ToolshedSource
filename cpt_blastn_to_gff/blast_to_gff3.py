#!/usr/bin/env python
import argparse
import copy
import logging
import re
import sys
from CPT_GFFParser import gffParse, gffWrite, gffSeqFeature
from Bio.Blast import NCBIXML
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from Bio.SeqFeature import SeqFeature, FeatureLocation

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(name="blast2gff3")

__doc__ = """
Convert BlastXML or Blast 25 Column Table output into GFF3
"""

# note for all FeatureLocations, Biopython saves in zero index and Blast provides one indexed locations, thus a Blast Location of (123,500) should be saved as (122, 500)
def blast2gff3(blast, blastxml=False, blasttab=False, include_seq=False):
    # Call correct function based on xml or tabular file input, raise error if neither or both are provided
    if blastxml and blasttab:
        raise Exception("Cannot provide both blast xml and tabular flag")

    if blastxml:
        return blastxml2gff3(blast, include_seq)
    elif blasttab:
        return blasttsv2gff3(blast, include_seq)
    else:
        raise Exception("Must provide either blast xml or tabular flag")


def check_bounds(ps, pe, qs, qe):
    # simplify the constant boundary checking used in subfeature generation
    if qs < ps:
        ps = qs
    if qe > pe:
        pe = qe
    if ps <= 0:
        ps = 1
    return (min(ps, pe), max(ps, pe))


def clean_string(s):
    clean_str = re.sub("\|", "_", s)  # Replace any \ or | with _
    clean_str = re.sub(
        "[^A-Za-z0-9_\ .-]", "", clean_str
    )  # Remove any non-alphanumeric or _.- chars
    return clean_str


def clean_slist(l):
    cleaned_list = []
    for s in l:
        cleaned_list.append(clean_string(s))
    return cleaned_list


def blastxml2gff3(blastxml, include_seq=False):

    blast_records = NCBIXML.parse(blastxml)
    for idx_record, record in enumerate(blast_records):
        # http://www.sequenceontology.org/browser/release_2.4/term/SO:0000343
        # match_type = {  # Currently we can only handle BLASTN, BLASTP
        #    "BLASTN": "nucleotide_match",
        #    "BLASTP": "protein_match",
        # }.get(record.application, "match")
        match_type = "match"
        collected_records = []

        recid = record.query
        if " " in recid:
            recid = clean_string(recid[0 : recid.index(" ")])

        for idx_hit, hit in enumerate(record.alignments):
            # gotta check all hsps in a hit to see boundaries
            rec = SeqRecord("", id=recid)
            parent_match_start = 0
            parent_match_end = 0
            hit_qualifiers = {
                "ID": "b2g.%s.%s" % (idx_record, idx_hit),
                "source": "blast",
                "accession": hit.accession,
                "hit_id": clean_string(hit.hit_id),
                "score": None,
                "length": hit.length,
                "hit_titles": clean_slist(hit.title.split(" >")),
                "hsp_count": len(hit.hsps),
            }
            desc = hit.title.split(" >")[0]
            hit_qualifiers["Name"] = desc
            sub_features = []
            for idx_hsp, hsp in enumerate(hit.hsps):
                if idx_hsp == 0:
                    # -2 and +1 for start/end to convert 0 index of python to 1 index of people, -2 on start because feature location saving issue
                    parent_match_start = hsp.query_start
                    parent_match_end = hsp.query_end
                    hit_qualifiers["score"] = hsp.expect
                # generate qualifiers to be added to gff3 feature
                hit_qualifiers["score"] = min(hit_qualifiers["score"], hsp.expect)
                hsp_qualifiers = {
                    "ID": "b2g.%s.%s.hsp%s" % (idx_record, idx_hit, idx_hsp),
                    "source": "blast",
                    "score": hsp.expect,
                    "accession": hit.accession,
                    "hit_id": clean_string(hit.hit_id),
                    "length": hit.length,
                    "hit_titles": clean_slist(hit.title.split(" >")),
                }
                if include_seq:
                    if (
                        "blast_qseq",
                        "blast_sseq",
                        "blast_mseq",
                    ) in hit_qualifiers.keys():
                        hit_qualifiers.update(
                            {
                                "blast_qseq": hit_qualifiers["blast_qseq"] + hsp.query,
                                "blast_sseq": hit_qualifiers["blast_sseq"] + hsp.sbjct,
                                "blast_mseq": hit_qualifiers["blast_mseq"] + hsp.match,
                            }
                        )
                    else:
                        hit_qualifiers.update(
                            {
                                "blast_qseq": hsp.query,
                                "blast_sseq": hsp.sbjct,
                                "blast_mseq": hsp.match,
                            }
                        )
                for prop in (
                    "score",
                    "bits",
                    "identities",
                    "positives",
                    "gaps",
                    "align_length",
                    "strand",
                    "frame",
                    "query_start",
                    "query_end",
                    "sbjct_start",
                    "sbjct_end",
                ):
                    hsp_qualifiers["blast_" + prop] = getattr(hsp, prop, None)

                # check if parent boundary needs to increase to envelope hsp
                # if hsp.query_start < parent_match_start:
                #    parent_match_start = hsp.query_start - 1
                # if hsp.query_end > parent_match_end:
                #    parent_match_end = hsp.query_end + 1

                parent_match_start, parent_match_end = check_bounds(
                    parent_match_start, parent_match_end, hsp.query_start, hsp.query_end
                )

                # add hsp to the gff3 feature as a "match_part"
                sub_features.append(
                    gffSeqFeature(
                        FeatureLocation(hsp.query_start - 1, hsp.query_end),
                        type="match_part",
                        strand=0,
                        qualifiers=copy.deepcopy(hsp_qualifiers),
                    )
                )

            # Build the top level seq feature for the hit
            hit_qualifiers["description"] = "Residue %s..%s hit to %s" % (
                parent_match_start,
                parent_match_end,
                desc,
            )
            top_feature = gffSeqFeature(
                FeatureLocation(parent_match_start - 1, parent_match_end),
                type=match_type,
                strand=0,
                qualifiers=hit_qualifiers,
            )
            # add the generated subfeature hsp match_parts to the hit feature
            top_feature.sub_features = copy.deepcopy(
                sorted(sub_features, key=lambda x: int(x.location.start))
            )
            # Add the hit feature to the record
            rec.features.append(top_feature)
            rec.annotations = {}
            collected_records.append(rec)

        if not len(collected_records):
            print("##gff-version 3\n##sequence-region null 1 4")

        for rec in collected_records:
            yield rec


def combine_records(records):
    # Go through each record and identify those records with
    cleaned_records = {}
    for rec in records:
        combo_id = (
            rec.features[0].qualifiers["target"]
            + rec.features[0].qualifiers["accession"]
        )
        if combo_id not in cleaned_records.keys():
            # First instance of a query ID + subject ID combination
            # Save this record as it's only item
            newid = rec.features[0].qualifiers["ID"] + ".0"
            rec.features[0].qualifiers["ID"] = newid
            rec.features[0].sub_features[0].qualifiers["ID"] = newid + ".hsp0"
            cleaned_records[combo_id] = rec
        else:
            # Query ID + Subject ID has appeared before
            # Combine the Match Parts as subfeatures
            sub_features = copy.deepcopy(
                cleaned_records[combo_id].features[0].sub_features
            )
            addtnl_features = rec.features[0].sub_features
            # add the current records sub features to the ones previous
            for feat in addtnl_features:
                sub_features.append(feat)
            cleaned_records[combo_id].features[0].subfeatures = copy.deepcopy(
                sub_features
            )
            cleaned_records[combo_id].features[0].qualifiers["score"] = min(
                cleaned_records[combo_id].features[0].qualifiers["score"],
                rec.features[0].qualifiers["score"],
            )
            # now we need to update the IDs for the features when combined
            # sort them into the proper order, then apply new ids
            # and also ensure the parent record boundaries fit the whole span of subfeatures
            sub_features = sorted(sub_features, key=lambda x: int(x.location.start))
            new_parent_start = cleaned_records[combo_id].features[0].location.start + 1
            new_parent_end = cleaned_records[combo_id].features[0].location.end
            for idx, feat in enumerate(sub_features):
                feat.qualifiers["ID"] = "%s.hsp%s" % (
                    cleaned_records[combo_id].features[0].qualifiers["ID"],
                    idx,
                )
                new_parent_start, new_parent_end = check_bounds(
                    new_parent_start,
                    new_parent_end,
                    feat.location.start + 1,
                    feat.location.end,
                )
                cleaned_records[combo_id].features[0].qualifiers["score"] = min(
                    cleaned_records[combo_id].features[0].qualifiers["score"],
                    feat.qualifiers["blast_score"],
                )
                # if feat.location.start < new_parent_start:
                #    new_parent_start = feat.location.start - 1
                # if feat.location.end > new_parent_end:
                #    new_parent_end = feat.location.end + 1
            cleaned_records[combo_id].features[0].location = FeatureLocation(
                new_parent_start - 1, new_parent_end
            )
            cleaned_records[combo_id].features[0].qualifiers[
                "description"
            ] = "Residue %s..%s hit to %s" % (
                new_parent_start,
                new_parent_end,
                cleaned_records[combo_id].features[0].qualifiers["Name"],
            )
            # save the renamed and ordered feature list to record
            cleaned_records[combo_id].features[0].sub_features = copy.deepcopy(
                sub_features
            )
    return sorted(
        cleaned_records.values(), key=lambda x: int(x.features[0].location.start)
    )


def blasttsv2gff3(blasttsv, include_seq=False):

    # http://www.sequenceontology.org/browser/release_2.4/term/SO:0000343
    # match_type = {  # Currently we can only handle BLASTN, BLASTP
    #    "BLASTN": "nucleotide_match",
    #    "BLASTP": "protein_match",
    # }.get(type, "match")
    match_type = "match"

    columns = [
        "qseqid",  # 01 Query Seq-id (ID of your sequence)
        "sseqid",  # 02 Subject Seq-id (ID of the database hit)
        "pident",  # 03 Percentage of identical matches
        "length",  # 04 Alignment length
        "mismatch",  # 05 Number of mismatches
        "gapopen",  # 06 Number of gap openings
        "qstart",  # 07 Start of alignment in query
        "qend",  # 08 End of alignment in query
        "sstart",  # 09 Start of alignment in subject (database hit)
        "send",  # 10 End of alignment in subject (database hit)
        "evalue",  # 11 Expectation value (E-value)
        "bitscore",  # 12 Bit score
        "sallseqid",  # 13 All subject Seq-id(s), separated by a ';'
        "score",  # 14 Raw score
        "nident",  # 15 Number of identical matches
        "positive",  # 16 Number of positive-scoring matches
        "gaps",  # 17 Total number of gaps
        "ppos",  # 18 Percentage of positive-scoring matches
        "qframe",  # 19 Query frame
        "sframe",  # 20 Subject frame
        "qseq",  # 21 Aligned part of query sequence
        "sseq",  # 22 Aligned part of subject sequence
        "qlen",  # 23 Query sequence length
        "slen",  # 24 Subject sequence length
        "salltitles",  # 25 All subject title(s), separated by a '<>'
    ]
    collected_records = []
    for record_idx, record in enumerate(blasttsv):
        if record.startswith("#"):
            continue

        dc = {k: v for (k, v) in zip(columns, (x.strip() for x in record.split("\t")))}

        rec = SeqRecord("", id=dc["qseqid"])

        feature_id = "b2g.%s" % (record_idx)
        hit_qualifiers = {
            "ID": feature_id,
            "Name": (dc["salltitles"].split("<>")[0]),
            "description": "Residue {sstart}..{send} hit to {x}".format(
                x=dc["salltitles"].split("<>")[0], **dc
            ),
            "source": "blast",
            "score": dc["evalue"],
            "accession": clean_string(dc["sseqid"]),
            "length": dc["qlen"],
            "hit_titles": clean_slist(dc["salltitles"].split("<>")),
            "target": clean_string(dc["qseqid"]),
        }
        hsp_qualifiers = {"source": "blast"}
        for key in dc.keys():
            # Add the remaining BLAST info to the GFF qualifiers
            if key in (
                "salltitles",
                "sallseqid",
                "sseqid",
                "qseqid",
                "qseq",
                "sseq",
            ):
                continue
            hsp_qualifiers["blast_%s" % key] = clean_string(dc[key])

        # Below numbers stored as strings, convert to proper form
        for (
            integer_numerical_key
        ) in "gapopen gaps length mismatch nident positive qend qframe qlen qstart score send sframe slen sstart".split(
            " "
        ):
            dc[integer_numerical_key] = int(dc[integer_numerical_key])

        for float_numerical_key in "bitscore evalue pident ppos".split(" "):
            dc[float_numerical_key] = float(dc[float_numerical_key])

        parent_match_start = dc["qstart"]
        parent_match_end = dc["qend"]

        parent_match_start, parent_match_end = check_bounds(
            parent_match_start, parent_match_end, dc["qstart"], dc["qend"]
        )

        # The ``match`` feature will hold one or more ``match_part``s
        top_feature = gffSeqFeature(
            FeatureLocation(
                min(parent_match_start, parent_match_end) - 1,
                max(parent_match_start, parent_match_end),
            ),
            type=match_type,
            strand=0,
            qualifiers=hit_qualifiers,
        )
        top_feature.sub_features = []
        # There is a possibility of multiple lines containing the HSPS
        # for the same hit.
        # Unlike the parent feature, ``match_part``s have sources.
        hsp_qualifiers["ID"] = clean_string(dc["sseqid"])
        match_part_start = dc["qstart"]
        match_part_end = dc["qend"]

        top_feature.sub_features.append(
            gffSeqFeature(
                FeatureLocation(
                    min(match_part_start, match_part_end) - 1,
                    max(match_part_start, match_part_end),
                ),
                type="match_part",
                strand=0,
                qualifiers=copy.deepcopy(hsp_qualifiers),
            )
        )
        top_feature.sub_features = sorted(
            top_feature.sub_features, key=lambda x: int(x.location.start)
        )
        rec.features = [top_feature]
        rec.annotations = {}
        collected_records.append(rec)

    collected_records = combine_records(collected_records)
    if not len(collected_records):
        print("##gff-version 3\n##sequence-region null 1 4")
    for rec in collected_records:
        yield rec


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert BlastP or BlastN output to GFF3, must provide XML or Tabular output",
        epilog="",
    )
    parser.add_argument(
        "blast",
        type=argparse.FileType("r"),
        help="Blast XML or 25 Column Tabular Output file",
    )
    parser.add_argument(
        "--blastxml", action="store_true", help="Process file as Blast XML Output"
    )
    parser.add_argument(
        "--blasttab",
        action="store_true",
        help="Process file as Blast 25 Column  Tabular Output",
    )
    parser.add_argument(
        "--include_seq",
        action="store_true",
        help="Include sequence, only used for Blast XML",
    )
    args = parser.parse_args()

    for rec in blast2gff3(**vars(args)):
        if len(rec.features):
            gffWrite([rec], sys.stdout)
