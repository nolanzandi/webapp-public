from collections import namedtuple
import taxcalc
import os
import requests
from requests.exceptions import Timeout, RequestException
import json
import pandas as pd
import time


#
# Prepare user params to send to DropQ/Taxcalc
#

NUM_BUDGET_YEARS = int(os.environ.get('NUM_BUDGET_YEARS', 10))
START_YEAR = int(os.environ.get('START_YEAR', 2015))
#Hard fail on lack of dropq workers
dropq_workers = os.environ.get('DROPQ_WORKERS', '')
DROPQ_WORKERS = dropq_workers.split(",")

TAXCALC_COMING_SOON_FIELDS = [
    '_Dividend_rt1', '_Dividend_thd1',
    '_Dividend_rt2', '_Dividend_thd2',
    '_Dividend_rt3', '_Dividend_thd3', '_BE_inc', '_BE_sub',
    '_BE_cg_per', '_BE_cg_trn'
    ]
TAXCALC_COMING_SOON_INDEXED_BY_MARS = [
    '_Dividend_thd1','_Dividend_thd2', '_Dividend_thd3'
]

TIMEOUT_IN_SECONDS = 1.0
MAX_ATTEMPTS_SUBMIT_JOB = 20

#
# Display TaxCalc result data
#

TAXCALC_RESULTS_START_YEAR = START_YEAR
TAXCALC_RESULTS_MTABLE_COL_LABELS = taxcalc.TABLE_LABELS
TAXCALC_RESULTS_DFTABLE_COL_LABELS = taxcalc.DIFF_TABLE_LABELS
TAXCALC_RESULTS_MTABLE_COL_FORMATS = [
    #   divisor,   unit,   decimals
    [      1000,      None, 0], # 'Returns',
    [1000000000, 'Dollars', 1],  # 'AGI',
    [      1000,      None, 0],  # 'Standard Deduction Filers',
    [1000000000, 'Dollars', 1],  # 'Standard Deduction',
    [      1000,      None, 0],  # 'Itemizers',
    [1000000000, 'Dollars', 1],  # 'Itemized Deduction',
    [1000000000, 'Dollars', 1],  # 'Personal Exemption',
    [1000000000, 'Dollars', 1],  # 'Taxable Income',
    [1000000000, 'Dollars', 1],  # 'Regular Tax',
    [1000000000, 'Dollars', 1],  # 'AMTI',
    [      1000,      None, 0],  # 'AMT Filers',
    [1000000000, 'Dollars', 1],  # 'AMT',
    [1000000000, 'Dollars', 1],  # 'Tax before Credits',
    [1000000000, 'Dollars', 1],  # 'Non-refundable Credits',
    [1000000000, 'Dollars', 1],  # 'Tax before Refundable Credits',
    [1000000000, 'Dollars', 1],  # 'Refundable Credits',
    [1000000000, 'Dollars', 1],  # 'Revenue'
]
TAXCALC_RESULTS_DFTABLE_COL_FORMATS = [
    [      1000,      None, 0],    # "Inds. w/ Tax Cut",
    [      1000,      None, 0],    # "Inds. w/ Tax Increase",
    [      1000,      None, 0],    # "Count",
    [         1, 'Dollars', 0],  # "Mean Tax Difference",
    [1000000000, 'Dollars', 1],  # "Total Tax Difference",
    [         1,   '%', 1],  # "%age Tax Increase",
    [         1,   '%', 1],  # "%age Tax Decrease",
    [         1,   '%', 1],  # "Share of Overall Change"
]
TAXCALC_RESULTS_BIN_ROW_KEYS = [
    "less_than_10", "ten_twenty", "twenty_thirty", "thirty_forty",
    "forty_fifty", "fifty_seventyfive", "seventyfive_hundred",
    "hundred_twohundred", "twohundred_fivehundred",
    "fivehundred_thousand", "thousand_up", "all"
]
TAXCALC_RESULTS_BIN_ROW_KEY_LABELS = {
    'less_than_10':'Less than 10',
    'ten_twenty':'10-20',
    'twenty_thirty':'20-30',
    'thirty_forty':'30-40',
    'forty_fifty':'40-50',
    'fifty_seventyfive':'50-75',
    'seventyfive_hundred':'75-100',
    'hundred_twohundred':'100-200',
    'twohundred_fivehundred':'200-500',
    'fivehundred_thousand':'500-1000',
    'thousand_up':'1000+',
    'all':'All'
}
TAXCALC_RESULTS_DEC_ROW_KEYS = [
    "perc0-10", "perc10-20", "perc20-30", "perc30-40",
    "perc40-50", "perc50-60", "perc60-70", "perc70-80",
    "perc80-90", "perc90-100", "all"
]
TAXCALC_RESULTS_DEC_ROW_KEY_LABELS = {
    'perc0-10':'0-10%',
    'perc10-20':'10-20%',
    'perc20-30':'20-30%',
    'perc30-40':'30-40%',
    'perc40-50':'40-50%',
    'perc50-60':'50-60%',
    'perc60-70':'60-70%',
    'perc70-80':'70-80%',
    'perc80-90':'80-90%',
    'perc90-100':'90-100%',
    'all':'All'
}
TAXCALC_RESULTS_TABLE_LABELS = {
    'mX_dec': 'Base plan tax vars, weighted avg per AGI decile',
    'mY_dec': 'User plan tax vars, weighted avg per AGI decile',
    'df_dec': 'Difference between Base and User plans by AGI decile',
    'mX_bin': 'Base plan tax vars, weighted avg per income bin',
    'mY_bin': 'User plan tax vars, weighted avg per income bin',
    'df_bin': 'Difference between Base and User plans by income bin',
    'fiscal_tots': 'Total Revenue Change by Calendar Year',
}



def expand_1D(x, num_years):
    """
    Expand the given data to account for the given number of budget years.
    Expanded entries are None by default
    """

    if len(x) >= num_years:
        return list(x)
    else:
        ans = [None] * num_years
        ans[:len(x)] = x
        return ans


def expand_2D(x, num_years):
    """
    Expand the given data to account for the given number of budget years.
    For 2D arrays, we expand out the number of rows until we have num_years
    number of rows. Added rows have all 'None' entries
    """

    if len(x) >= num_years:
        return list(x)
    else:
        ans = []
        for i in range(0, num_years):
            ans.append([None] * len(x[0]))
        for i, arr in enumerate(x):
            ans[i] = arr
        return ans


def expand_list(x, num_years):
    """
    Dispatch to either expand_1D or expand2D depending on the dimension of x

    Parameters:
    -----------
    x : value to expand

    num_years: int
    Number of budget years to expand

    Returns:
    --------
    expanded list
    """
    if isinstance(x[0], list):
        return expand_2D(x, num_years)
    else:
        return expand_1D(x, num_years)


def convert_to_floats(tsi):
    """
    A helper function that tax all of the fields of a TaxSaveInputs model
    and converts them to floats, or list of floats
    """
    def numberfy_one(x):
        if isinstance(x, float):
            return x
        else:
            return float(x)

    def numberfy(x):
        if isinstance(x, list):
            return [numberfy_one(i) for i in x]
        else:
            return numberfy_one(x)

    attrs = vars(tsi)
    return { k:numberfy(v) for k,v in attrs.items() if v}


def leave_name_in(key, val, dd):
    """
    Under certain conditions, we will remove 'key' and its value
    from the dictionary we pass to the dropq package. This function
    will test those conditions and return a Bool.

    Parameters:
    -----------
    key: a field name to potentially pass to the dropq package

    dd: the default dictionary of data in taxcalc Parameters

    Returns:
    --------
    Bool: True if we allow this field to get passed on. False
          if it should be removed.
    """

    if key in dd:
        return True
    else:
        print "Don't have this pair: ", key, val
        underscore_name_in_defaults = "_" + key in dd
        is_cpi_name = key.endswith("_cpi")
        is_array_name = (key.endswith("_0") or key.endswith("_1") or
                         key.endswith("_2") or key.endswith("_3"))
        if (underscore_name_in_defaults or is_cpi_name or is_array_name):
            return True
        else:
            return False


def package_up_vars(user_values):
    dd = taxcalc.parameters.default_data(start_year=START_YEAR)
    for k, v in user_values.items():
        if not leave_name_in(k, v, dd):
            print "Removing ", k, v
            del user_values[k]
    name_stems = {}
    ans = {}
    #Find the 'broken out' array values, these have special treatment
    for k, v in user_values.items():
        if (k.endswith("_0") or k.endswith("_1") or k.endswith("_2")
                or k.endswith("_3")):
            vals = name_stems.setdefault(k[:-2], [])
            vals.append(k)

    #For each array value, expand as necessary based on default data
    #then add user values. It is acceptable to leave 'blanks' as None.
    #This is handled on the taxcalc side
    for k, vals in name_stems.items():
        if k in dd:
            default_data = dd[k]
            param = k
        else:
            #add a leading underscore
            default_data = dd["_" + k]
            param = "_" + k

        # get max number of years to advance
        _max = 0
        for name in vals:
            num_years = len(user_values[name])
            if num_years > _max:
                _max = num_years
        expnded = expand_list(default_data, _max)
        #Now copy necessary data to expanded array
        for name in vals:
            idx = int(name[-1]) # either 0, 1, 2, 3
            user_arr = user_values[name]
            for new_arr, user_val in zip(expnded, user_arr):
                new_arr[idx] = int(user_val)
            del user_values[name]
        ans[param] = expnded

    #Process remaining values set by user
    for k, v in user_values.items():
        if k in dd:
            default_data = dd[k]
            param = k
        elif k.endswith("_cpi"):
            if k[:-4] in dd:
                ans[k] = v
            else:
                ans['_' + k] = v
            continue
        else:
            #add a leading underscore
            default_data = dd["_" + k]
            param = "_" + k
        num_years = len(v)
        expnded = expand_list(default_data, num_years)
        for i, new_val in enumerate(v):
            expnded[i] = new_val
        ans[param] = expnded

    return ans


#
# Gather data to assist in displaying TaxCalc param form
#

class TaxCalcField(object):
    """
    An atomic unit of data for a TaxCalcParam, which can be stored as a field
    Used for both CSV float fields (value column data) and boolean fields (cpi)
    """
    def __init__(self, id, label, values, param):
        self.id = id
        self.label = label
        self.values = values
        self.param = param

        self.values_by_year = {}
        for i, value in enumerate(values):
            year = param.start_year + i
            self.values_by_year[year] = value

        self.default_value = self.values_by_year[START_YEAR]


class TaxCalcParam(object):
    """
    A collection of TaxCalcFields that represents all configurable details
    for one of TaxCalc's Parameters
    """
    def __init__(self, param_id, attributes):
        self.__load_from_json(param_id, attributes)

    def __load_from_json(self, param_id, attributes):
        values_by_year = attributes['value']
        col_labels = attributes['col_label']

        self.tc_id = param_id
        self.nice_id = param_id[1:] if param_id[0] == '_' else param_id
        self.name = attributes['long_name']
        self.info = " ".join([
            attributes['description'],
            attributes.get('irs_ref') or "",  # sometimes this is blank
            attributes.get('notes') or ""     # sometimes this is blank
            ]).strip()

        # Pretend the start year is 2015 (instead of 2013),
        # until values for that year are provided by taxcalc
        #self.start_year = int(attributes['start_year'])
        self.start_year = START_YEAR

        self.coming_soon = (self.tc_id in TAXCALC_COMING_SOON_FIELDS)

        # normalize single-year default lists [] to [[]]
        if not isinstance(values_by_year[0], list):
            values_by_year = [values_by_year]

        # organize defaults by column [[A1,B1],[A2,B2]] to [[A1,A2],[B1,B2]]
        values_by_col = [list(x) for x in zip(*values_by_year)]

        #
        # normalize and format column labels
        #
        if self.tc_id in TAXCALC_COMING_SOON_INDEXED_BY_MARS:
            col_labels = ["Single", "Married filing Jointly",
                              "Married filing Separately", "Head of Household"]
            values_by_col = ['0','0','0','0']

        elif isinstance(col_labels, list):
            if col_labels == ["0kids", "1kid", "2kids", "3+kids"]:
                col_labels = ["0 Kids", "1 Kid", "2 Kids", "3+ Kids"]

            elif col_labels == ["single", "joint", "separate", "head of household",
                                "widow", "separate"] or col_labels == \
                               ["single", "joint", "separate", "head of household",
                               "widow", "separate","dependent"]:

                col_labels = ["Single", "Married filing Jointly",
                              "Married filing Separately", "Head of Household"]

        else:
            if col_labels == "NA" or col_labels == "":
                col_labels = [""]
            elif col_labels == "0kids 1kid  2kids 3+kids":
                col_labels =  ["0 Kids", "1 Kid", "2 Kids", "3+ Kids"]


        # create col params
        self.col_fields = []

        if len(col_labels) == 1:
            self.col_fields.append(TaxCalcField(
                self.nice_id,
                col_labels[0],
                values_by_col[0],
                self
            ))
        else:
            for col, label in enumerate(col_labels):
                self.col_fields.append(TaxCalcField(

                    self.nice_id + "_{0}".format(col),
                    label,
                    values_by_col[col],
                    self
                ))

        # we assume we can CPI inflate if first value isn't a ratio
        first_value = self.col_fields[0].values[0]
        self.inflatable = first_value > 1 and self.tc_id != '_ACTC_ChildNum'

        if self.inflatable:
            self.cpi_field = TaxCalcField(self.nice_id + "_cpi", "CPI", [True], self)

# Create a list of default parameters
TAXCALC_DEFAULT_PARAMS_JSON = taxcalc.parameters.default_data(metadata=True, start_year=2015)
default_taxcalc_params = {}
for k,v in TAXCALC_DEFAULT_PARAMS_JSON.iteritems():
    param = TaxCalcParam(k,v)
    default_taxcalc_params[param.nice_id] = param

#Behavior Effects not in params.json yet. Add in the appropriate info so that
#the params dictionary has the right info
# value, col_label, long_name, description, irs_ref, notes
be_params = []
be_inc_param = {'value':[0], 'col_label':['label'], 'long_name':'Income Effect',
                'description': 'Behavior Effects', 'irs_ref':'', 'notes':''}
be_sub_param = {'value':[0], 'col_label':['label'], 'long_name':'Substitution Effect',
                'description': 'Behavior Effects', 'irs_ref':'', 'notes':''}
be_cg_per_param = {'value':[0], 'col_label':['label'], 'long_name':'Persistent',
                'description': 'Behavior Effects', 'irs_ref':'', 'notes':''}
be_cg_trn_param= {'value':[0], 'col_label':['label'], 'long_name':'Transitory',
                'description': 'Behavior Effects', 'irs_ref':'', 'notes':''}
be_params.append(('_BE_inc', be_inc_param))
be_params.append(('_BE_sub', be_sub_param))
be_params.append(('_BE_cg_per', be_cg_per_param))
be_params.append(('_BE_cg_trn', be_cg_trn_param))
for k,v in be_params:
    param = TaxCalcParam(k,v)
    default_taxcalc_params[param.nice_id] = param

TAXCALC_DEFAULT_PARAMS = default_taxcalc_params


# Debug TaxParams
"""
for k, param in TAXCALC_DEFAULT_PARAMS.iteritems():
    print(' -- ' + k + ' -- ')
    print('TC id:   ' + param.tc_id)
    print('Nice id: ' + param.nice_id)
    print('name:    ' + param.name)
    print('info:    ' + param.info + '\n')

    if param.inflatable:
        field = param.cpi_field
        print(field.id + ' - ' + field.label + ' - ' + str(field.values))
    for field in param.col_fields:
        print(field.id + '   - ' + field.label + ' - ' + str(field.values))

    print('\n')
"""


def taxcalc_results_to_tables(results):
    """
    Take various results from dropq, i.e. mY_dec, mX_bin, df_dec, etc
    Return organized and labeled table results for display
    """

    num_years = len(results['fiscal_tots'])
    years = list(range(TAXCALC_RESULTS_START_YEAR,
                       TAXCALC_RESULTS_START_YEAR + num_years))

    tables = {}
    for table_id in results:

        # Debug inputs
        """
        print('\n ----- inputs ------- ')
        print('looking at {0}'.format(table_id))
        if table_id == 'fiscal_tots':
            print('{0}'.format(results[table_id]))
        else:
            print('{0}'.format(results[table_id].keys()))
        print(' ----- inputs ------- \n')
        """

        if table_id in ['mX_dec', 'mY_dec']:
            row_keys = TAXCALC_RESULTS_DEC_ROW_KEYS
            row_labels = TAXCALC_RESULTS_DEC_ROW_KEY_LABELS
            col_labels = TAXCALC_RESULTS_MTABLE_COL_LABELS
            col_formats = TAXCALC_RESULTS_MTABLE_COL_FORMATS
            table_data = results[table_id]
            multi_year_cells = True

        elif table_id in ['mX_bin', 'mY_bin']:
            row_keys = TAXCALC_RESULTS_BIN_ROW_KEYS
            row_labels = TAXCALC_RESULTS_BIN_ROW_KEY_LABELS
            col_labels = TAXCALC_RESULTS_MTABLE_COL_LABELS
            col_formats = TAXCALC_RESULTS_MTABLE_COL_FORMATS
            table_data = results[table_id]
            multi_year_cells = True

        elif table_id == 'df_dec':
            row_keys = TAXCALC_RESULTS_DEC_ROW_KEYS
            row_labels = TAXCALC_RESULTS_DEC_ROW_KEY_LABELS
            col_labels = TAXCALC_RESULTS_DFTABLE_COL_LABELS
            col_formats = TAXCALC_RESULTS_DFTABLE_COL_FORMATS
            table_data = results[table_id]
            multi_year_cells = True

        elif table_id == 'df_bin':
            row_keys = TAXCALC_RESULTS_BIN_ROW_KEYS
            row_labels = TAXCALC_RESULTS_BIN_ROW_KEY_LABELS
            col_labels = TAXCALC_RESULTS_DFTABLE_COL_LABELS
            col_formats = TAXCALC_RESULTS_DFTABLE_COL_FORMATS
            table_data = results[table_id]
            multi_year_cells = True

        elif table_id == 'fiscal_tots':
            # todo - move these into the above TC result param constants
            row_keys = ['totals']
            row_labels = {'totals': 'Total Revenue'}
            col_labels = years
            col_formats = [ [1000000000, 'Dollars', 1] for y in years]
            table_data = {'totals': results[table_id]}
            multi_year_cells = False

        table = {
            'col_labels': col_labels,
            'cols': [],
            'label': TAXCALC_RESULTS_TABLE_LABELS[table_id],
            'rows': [],
            'multi_valued': multi_year_cells
        }

        for col_key, label in enumerate(col_labels):
            table['cols'].append({
                'label': label,
                'divisor': col_formats[col_key][0],
                'units': col_formats[col_key][1],
                'decimals': col_formats[col_key][2],
            })

        col_count = len(col_labels)
        for row_key in row_keys:
            row = {
                'label': row_labels[row_key],
                'cells': []
            }

            for col_key in range(0, col_count):
                cell = {
                    'year_values': {},
                    'format': {
                        'divisor': table['cols'][col_key]['divisor'],
                        'decimals': table['cols'][col_key]['decimals'],
                    }
                }

                if multi_year_cells:
                    for yi, year in enumerate(years):
                        value = table_data["{0}_{1}".format(row_key, yi)][col_key]
                        if value[-1] == "%":
                            value = value[:-1]
                        cell['year_values'][year] = value

                    cell['first_value'] = cell['year_values'][TAXCALC_RESULTS_START_YEAR]

                else:
                    value = table_data[row_key][col_key]
                    if value[-1] == "%":
                            value = value[:-1]
                    cell['value'] = value

                row['cells'].append(cell)

            table['rows'].append(row)

        tables[table_id] = table

        # Debug results
        """
        print('\n ----- result ------- ')
        print('{0}'.format(table))
        print(' ----- result ------- \n')
        """

    tables['result_years'] = years
    return tables

def format_csv(tax_results, url_id):
    """
    Takes a dictionary with the tax_results, having these keys:
    [u'mY_bin', u'mX_bin', u'mY_dec', u'mX_dec', u'df_dec', u'df_bin',
    u'fiscal_tots']
    And then returns a list of list of strings for CSV output. The format
    of the lines is as follows:
    #URL: http://www.ospc.org/taxbrain/ID/csv/
    #fiscal tots data
    YEAR_0, ... YEAR_K
    val, val, ... val
    #mX_dec
    YEAR_0
    col_0, col_1, ..., col_n
    val, val, ..., val
    YEAR_1
    col_0, col_1, ..., col_n
    val, val, ..., val
    ...
    #mY_dec
    YEAR_0
    col_0, col_1, ..., col_n
    val, val, ..., val
    YEAR_1
    col_0, col_1, ..., col_n
    val, val, ..., val
    ...
    #df_dec
    YEAR_0
    col_0, col_1, ..., col_n
    val, val, ..., val
    YEAR_1
    col_0, col_1, ..., col_n
    val, val, ..., val
    ...
    #mX_bin
    YEAR_0
    col_0, col_1, ..., col_n
    val, val, ..., val
    YEAR_1
    col_0, col_1, ..., col_n
    val, val, ..., val
    ...
    #mY_bin
    YEAR_0
    col_0, col_1, ..., col_n
    val, val, ..., val
    YEAR_1
    col_0, col_1, ..., col_n
    val, val, ..., val
    ...
    #df_bin
    YEAR_0
    col_0, col_1, ..., col_n
    val, val, ..., val
    YEAR_1
    col_0, col_1, ..., col_n
    val, val, ..., val
    ...
    """
    res = []

    #URL
    res.append(["#URL: http://www.ospc.org/taxbrain/" + str(url_id) + "/"])

    #FISCAL TOTS
    res.append(["#fiscal totals data"])
    ft = tax_results.get('fiscal_tots', [])
    yrs = [START_YEAR + i for i in range(0, len(ft))]
    if yrs:
        res.append(yrs)
    if ft:
        res.append(ft)

    #MX_DEC
    res.append(["#mX_dec"])
    mxd = tax_results.get('mX_dec', {})
    if mxd:
        for count, yr in enumerate(yrs):
            res.append([yr])
            res.append(TAXCALC_RESULTS_MTABLE_COL_LABELS)
            for row in TAXCALC_RESULTS_DEC_ROW_KEYS:
                res.append(mxd[row+"_" + str(count)])

    #MY_DEC
    res.append(["#mY_dec"])
    myd = tax_results.get('mY_dec', {})
    if myd:
        for count, yr in enumerate(yrs):
            res.append([yr])
            res.append(TAXCALC_RESULTS_MTABLE_COL_LABELS)
            for row in TAXCALC_RESULTS_DEC_ROW_KEYS:
                res.append(myd[row+"_" + str(count)])

    #DF_DEC
    res.append(["#df_dec"])
    dfd = tax_results.get('df_dec', {})
    if dfd:
        for count, yr in enumerate(yrs):
            res.append([yr])
            res.append(TAXCALC_RESULTS_DFTABLE_COL_LABELS)
            for row in TAXCALC_RESULTS_DEC_ROW_KEYS:
                res.append(dfd[row+"_" + str(count)])

    #MX_BIN
    res.append(["#mX_bin"])
    mxb = tax_results.get('mX_bin', {})
    if mxb:
        for count, yr in enumerate(yrs):
            res.append([yr])
            res.append(TAXCALC_RESULTS_MTABLE_COL_LABELS)
            for row in TAXCALC_RESULTS_BIN_ROW_KEYS:
                res.append(mxb[row+"_" + str(count)])

    #MY_BIN
    res.append(["#mY_bin"])
    myb = tax_results.get('mY_bin', {})
    if myb:
        for count, yr in enumerate(yrs):
            res.append([yr])
            res.append(TAXCALC_RESULTS_MTABLE_COL_LABELS)
            for row in TAXCALC_RESULTS_BIN_ROW_KEYS:
                res.append(myb[row+"_" + str(count)])

    #DF_BIN
    res.append(["#df_bin"])
    dfb = tax_results.get('df_bin', {})
    if dfb:
        for count, yr in enumerate(yrs):
            res.append([yr])
            res.append(TAXCALC_RESULTS_DFTABLE_COL_LABELS)
            for row in TAXCALC_RESULTS_BIN_ROW_KEYS:
                res.append(dfb[row+"_" + str(count)])

    return res

def submit_dropq_calculation(mods):
    print "mods is ", mods
    user_mods = package_up_vars(mods)
    if not bool(user_mods):
        return False
    print "user_mods is ", user_mods
    print "submit work"
    user_mods={START_YEAR:user_mods}
    years = list(range(0,NUM_BUDGET_YEARS))

    hostnames = DROPQ_WORKERS
    num_hosts = len(hostnames)
    data = {}
    data['user_mods'] = json.dumps(user_mods)
    job_ids = []
    hostname_idx = 0
    for y in years:
        year_submitted = False
        attempts = 0
        while not year_submitted:
            data['year'] = str(y)
            theurl = "http://{hn}/dropq_start_job".format(hn=hostnames[hostname_idx])
            try:
                response = requests.post(theurl, data=data, timeout=TIMEOUT_IN_SECONDS)
                if response.status_code == 200:
                    print "submitted: ", str(y), hostnames[hostname_idx]
                    year_submitted = True
                    job_ids.append((response.text, hostnames[hostname_idx]))
                    hostname_idx = (hostname_idx + 1) % num_hosts
                else:
                    print "FAILED: ", str(y), hostnames[hostname_idx]
                    hostname_idx = (hostname_idx + 1) % num_hosts
                    attempts += 1
            except Timeout:
                print "Couldn't submit to: ", hostnames[hostname_idx]
                hostname_idx = (hostname_idx + 1) % num_hosts
                attempts += 1
            except RequestException as re:
                print "Something unexpected happened: ", re
                hostname_idx = (hostname_idx + 1) % num_hosts
                attempts += 1
            if attempts > MAX_ATTEMPTS_SUBMIT_JOB:
                print "Exceeded max attempts. Bailing out."
                raise IOError()

    return job_ids

def dropq_results_ready(job_ids):
    jobs_done = [False] * len(job_ids)
    for idx, id_hostname in enumerate(job_ids):
        id_, hostname = id_hostname
        result_url = "http://{hn}/dropq_query_result".format(hn=hostname)
        job_response = requests.get(result_url, params={'job_id':id_})
        if job_response.status_code == 200: # Valid response
            rep = job_response.text
            if rep == 'YES':
                jobs_done[idx] = True
                print "got one!: ", id_

    return all(jobs_done)

def dropq_get_results(job_ids):
    ans = []
    for idx, id_hostname in enumerate(job_ids):
        id_, hostname = id_hostname
        result_url = "http://{hn}/dropq_get_result".format(hn=hostname)
        job_response = requests.get(result_url, params={'job_id':id_})
        if job_response.status_code == 200: # Valid response
            ans.append(job_response.json())

    mY_dec = {}
    mX_dec = {}
    df_dec = {}
    mY_bin = {}
    mX_bin = {}
    df_bin = {}
    fiscal_tots = []
    for result in ans:
        mY_dec.update(result['mY_dec'])
        mX_dec.update(result['mX_dec'])
        df_dec.update(result['df_dec'])
        mY_bin.update(result['mY_bin'])
        mX_bin.update(result['mX_bin'])
        df_bin.update(result['df_bin'])
        fiscal_tots.append(result['fiscal_tots'])

    results = {'mY_dec': mY_dec, 'mX_dec': mX_dec, 'df_dec': df_dec,
               'mY_bin': mY_bin, 'mX_bin': mX_bin, 'df_bin': df_bin,
               'fiscal_tots': fiscal_tots}

    return results
